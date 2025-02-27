import sympy
import antlr4
import re
import yaml
from antlr4.error.ErrorListener import ErrorListener

from gen.PSParser import PSParser
from gen.PSLexer import PSLexer
from gen.PSListener import PSListener

from sympy.printing.str import StrPrinter
import configparser

from sympytossml import QuantityModes, convert_sympy_ssml
from logger import logging, writelog

CONFIG_FILE = 'app_config.cfg'

def process_sympy(sympy):
    matherror = MathErrorListener(sympy)

    stream = antlr4.InputStream(sympy)
    lex    = PSLexer(stream)
    lex.removeErrorListeners()
    lex.addErrorListener(matherror)

    tokens = antlr4.CommonTokenStream(lex)
    parser = PSParser(tokens)

    # remove default console error listener
    parser.removeErrorListeners()
    parser.addErrorListener(matherror)

    relation = parser.math().relation()
    expr = convert_relation(relation)

    return expr

class MathErrorListener(ErrorListener):
    def __init__(self, src):
        super(ErrorListener, self).__init__()
        self.src = src

    def syntaxError(self, recog, symbol, line, col, msg, e):
        fmt = "%s\n%s\n%s"
        marker = "~" * col + "^"
        
        if msg.startswith("missing"):
            err = fmt % (msg, self.src, marker)
        elif msg.startswith("no viable"):
            err = fmt % ("I expected something else here", self.src, marker)
        elif msg.startswith("mismatched"):
            names = PSParser.literalNames
            expected = [names[i] for i in e.getExpectedTokens() if i < len(names)]
            if expected < 10:
                expected = " ".join(expected)
                err = (fmt % ("I expected one of these: " + expected,
                    self.src, marker))
            else:
                err = (fmt % ("I expected something else here", self.src, marker))
        else:
            err = fmt % ("I don't understand this", self.src, marker)
        raise Exception(err)

def convert_relation(rel):
    if rel.expr():
        return convert_expr(rel.expr())

    lh = convert_relation(rel.relation(0))
    rh = convert_relation(rel.relation(1))
    if rel.LT():
        return sympy.StrictLessThan(lh, rh)
    elif rel.LTE():
        return sympy.LessThan(lh, rh)
    elif rel.GT():
        return sympy.StrictGreaterThan(lh, rh) 
    elif rel.GTE():
        return sympy.GreaterThan(lh, rh)
    elif rel.EQUAL():
        return sympy.Eq(lh, rh)

def convert_expr(expr):
    return convert_add(expr.additive())

def convert_add(add):
    if add.ADD():
       lh = convert_add(add.additive(0))
       rh = convert_add(add.additive(1))
       return sympy.Add(lh, rh, evaluate=False)
    elif add.SUB():
        lh = convert_add(add.additive(0))
        rh = convert_add(add.additive(1))
        return sympy.Add(lh, -1 * rh, evaluate=False)
    else:
        return convert_mp(add.mp())

def convert_mp(mp):
    if hasattr(mp, 'mp'):
        mp_left = mp.mp(0)
        mp_right = mp.mp(1)
    else:
        mp_left = mp.mp_nofunc(0)
        mp_right = mp.mp_nofunc(1)

    if mp.MUL() or mp.CMD_TIMES() or mp.CMD_CDOT():
        lh = convert_mp(mp_left)
        rh = convert_mp(mp_right)
        return sympy.Mul(lh, rh, evaluate=False)
    elif mp.DIV() or mp.CMD_DIV() or mp.COLON():
        lh = convert_mp(mp_left)
        rh = convert_mp(mp_right)
        return sympy.Mul(lh, sympy.Pow(rh, -1, evaluate=False), evaluate=False)
    else:
        if hasattr(mp, 'unary'):
            return convert_unary(mp.unary())
        else:
            return convert_unary(mp.unary_nofunc())

def convert_unary(unary):
    if hasattr(unary, 'unary'):
        nested_unary = unary.unary()
    else:
        nested_unary = unary.unary_nofunc()
    if hasattr(unary, 'postfix_nofunc'):
        first = unary.postfix()
        tail = unary.postfix_nofunc()
        postfix = [first] + tail
    else:
        postfix = unary.postfix()

    if unary.ADD():
        return convert_unary(nested_unary)
    elif unary.SUB():
        return sympy.Mul(-1, convert_unary(nested_unary), evaluate=False)
    elif postfix:
        return convert_postfix_list(postfix)

def convert_postfix_list(arr, i=0):
    if i >= len(arr):
        raise Exception("Index out of bounds")

    res = convert_postfix(arr[i])
    if isinstance(res, sympy.Expr):
        if i == len(arr) - 1:
            return res # nothing to multiply by
        else:
            if i > 0:
                left = convert_postfix(arr[i - 1])
                right = convert_postfix(arr[i + 1])
                if isinstance(left, sympy.Expr) and isinstance(right, sympy.Expr):
                    left_syms  = convert_postfix(arr[i - 1]).atoms(sympy.Symbol)
                    right_syms = convert_postfix(arr[i + 1]).atoms(sympy.Symbol)
                    # if the left and right sides contain no variables and the
                    # symbol in between is 'x', treat as multiplication.
                    if len(left_syms) == 0 and len(right_syms) == 0 and str(res) == "x":
                        return convert_postfix_list(arr, i + 1)
            # multiply by next
            return sympy.Mul(res, convert_postfix_list(arr, i + 1), evaluate=False)
    else: # must be derivative
        wrt = res[0]
        if i == len(arr) - 1:
            raise Exception("Expected expression for derivative")
        else:
            expr = convert_postfix_list(arr, i + 1)
            return sympy.Derivative(expr, wrt)

def do_subs(expr, at):
    if at.expr():
        at_expr = convert_expr(at.expr())
        syms = at_expr.atoms(sympy.Symbol)
        if len(syms) == 0:
            return expr
        elif len(syms) > 0:
            sym = next(iter(syms))
            return expr.subs(sym, at_expr)
    elif at.equality():
        lh = convert_expr(at.equality().expr(0))
        rh = convert_expr(at.equality().expr(1))
        return expr.subs(lh, rh)

def convert_postfix(postfix):
    if hasattr(postfix, 'exp'):
        exp_nested = postfix.exp()
    else:
        exp_nested = postfix.exp_nofunc()

    exp = convert_exp(exp_nested)
    for op in postfix.postfix_op():
        if op.BANG():
            if isinstance(exp, list):
                raise Exception("Cannot apply postfix to derivative")
            exp = sympy.factorial(exp, evaluate=False)
        elif op.eval_at():
            ev = op.eval_at()
            at_b = None
            at_a = None
            if ev.eval_at_sup():
                at_b = do_subs(exp, ev.eval_at_sup()) 
            if ev.eval_at_sub():
                at_a = do_subs(exp, ev.eval_at_sub())
            if at_b != None and at_a != None:
                exp = sympy.Add(at_b, -1 * at_a, evaluate=False)
            elif at_b != None:
                exp = at_b
            elif at_a != None:
                exp = at_a
            
    return exp

def convert_exp(exp):
    if hasattr(exp, 'exp'):
        exp_nested = exp.exp()
    else:
        exp_nested = exp.exp_nofunc()

    if exp_nested:
        base = convert_exp(exp_nested)
        if isinstance(base, list):
            raise Exception("Cannot raise derivative to power")
        if exp.atom():
            exponent = convert_atom(exp.atom())
        elif exp.expr():
            exponent = convert_expr(exp.expr())
        return sympy.Pow(base, exponent, evaluate=False)
    else:
        if hasattr(exp, 'comp'):
            return convert_comp(exp.comp())
        else:
            return convert_comp(exp.comp_nofunc())

def convert_comp(comp):
    if comp.group():
        return convert_expr(comp.group().expr())
    elif comp.abs_group():
        return sympy.Abs(convert_expr(comp.abs_group().expr()), evaluate=False)
    elif comp.atom():
        return convert_atom(comp.atom())
    elif comp.frac():
        return convert_frac(comp.frac())
    elif comp.func():
        return convert_func(comp.func())

def convert_atom(atom):
    if atom.LETTER():
        subscriptName = ''
        if atom.subexpr():
            subscript = None
            if atom.subexpr().expr():           # subscript is expr
                subscript = convert_expr(atom.subexpr().expr())
            else:                               # subscript is atom
                subscript = convert_atom(atom.subexpr().atom())
            subscriptName = '_{' + StrPrinter().doprint(subscript) + '}'
        return sympy.Symbol(atom.LETTER().getText() + subscriptName)
    elif atom.SYMBOL():
        s = atom.SYMBOL().getText()[1:]
        if s == "infty":
            return sympy.oo
        else:
            if atom.subexpr():
                subscript = None
                if atom.subexpr().expr():           # subscript is expr
                    subscript = convert_expr(atom.subexpr().expr())
                else:                               # subscript is atom
                    subscript = convert_atom(atom.subexpr().atom())
                subscriptName = StrPrinter().doprint(subscript)
                s += '_{' + subscriptName + '}'
            return sympy.Symbol(s)
    elif atom.NUMBER():
        s = atom.NUMBER().getText().replace(",", "")
        return sympy.Number(s)
    elif atom.DIFFERENTIAL():
        var = get_differential_var(atom.DIFFERENTIAL())
        return sympy.Symbol('d' + var.name)
    elif atom.mathit():
        text = rule2text(atom.mathit().mathit_text())
        return sympy.Symbol(text)

def rule2text(ctx):
    stream = ctx.start.getInputStream()
    # starting index of starting token
    startIdx = ctx.start.start 
    # stopping index of stopping token
    stopIdx = ctx.stop.stop

    return stream.getText(startIdx, stopIdx)

def convert_frac(frac):
    diff_op = False
    partial_op = False
    lower_itv = frac.lower.getSourceInterval()
    lower_itv_len = lower_itv[1] - lower_itv[0] + 1
    if (frac.lower.start == frac.lower.stop and
        frac.lower.start.type == PSLexer.DIFFERENTIAL):
        wrt = get_differential_var_str(frac.lower.start.text)
        diff_op = True
    elif (lower_itv_len == 2 and
        frac.lower.start.type == PSLexer.SYMBOL and
        frac.lower.start.text == '\\partial' and
        (frac.lower.stop.type == PSLexer.LETTER or frac.lower.stop.type == PSLexer.SYMBOL)):
        partial_op = True
        wrt = frac.lower.stop.text
        if frac.lower.stop.type == PSLexer.SYMBOL:
            wrt = wrt[1:]

    if diff_op or partial_op:
        wrt = sympy.Symbol(wrt)
        if (diff_op and frac.upper.start == frac.upper.stop and
            frac.upper.start.type == PSLexer.LETTER and
            frac.upper.start.text == 'd'):
            return [wrt]
        elif (partial_op and frac.upper.start == frac.upper.stop and
            frac.upper.start.type == PSLexer.SYMBOL and
            frac.upper.start.text == '\\partial'):
            return [wrt]
        upper_text = rule2text(frac.upper)

        expr_top = None
        if diff_op and upper_text.startswith('d'):
            expr_top = process_sympy(upper_text[1:])
        elif partial_op and frac.upper.start.text == '\\partial':
            expr_top = process_sympy(upper_text[len('\\partial'):])
        if expr_top:
            return sympy.Derivative(expr_top, wrt)

    expr_top = convert_expr(frac.upper)
    expr_bot = convert_expr(frac.lower)
    return sympy.Mul(expr_top, sympy.Pow(expr_bot, -1, evaluate=False), evaluate=False)

def convert_binom(binom):
    expr_n = convert_expr(binom.n)
    expr_k = convert_expr(binom.k)
    return sympy.binomial(expr_n, expr_k, evaluate=False)

def convert_floor(floor):
    val = convert_expr(floor.val)
    return sympy.floor(val, evaluate=False)

def convert_ceil(ceil):
    val = convert_expr(ceil.val)
    return sympy.ceiling(val, evaluate=False)

def convert_func(func):
    if func.func_normal():
        if func.L_PAREN(): # function called with parenthesis
            arg = convert_func_arg(func.func_arg())
        else:
            arg = convert_func_arg(func.func_arg_noparens())
            
        name = func.func_normal().start.text[1:]

        # change arc<trig> -> a<trig>
        if name in ["arcsin", "arccos", "arctan", "arccsc", "arcsec",
        "arccot"]:
            name = "a" + name[3:]
            expr = getattr(sympy.functions, name)(arg, evaluate=False)
        if name in ["arsinh", "arcosh", "artanh"]:
            name = "a" + name[2:]
            expr = getattr(sympy.functions, name)(arg, evaluate=False)
            
        if (name=="log" or name=="ln"):
            if func.subexpr():
                base = convert_expr(func.subexpr().expr())
            elif name == "log":
                base = 10
            elif name == "ln":
                base = sympy.E
            expr = sympy.log(arg, base, evaluate=False)

        func_pow = None
        should_pow = True
        if func.supexpr():
            if func.supexpr().expr():
                func_pow = convert_expr(func.supexpr().expr())
            else:
                func_pow = convert_atom(func.supexpr().atom())

        if name in ["sin", "cos", "tan", "csc", "sec", "cot", "sinh", "cosh", "tanh"]:
                if func_pow == -1:
                    name = "a" + name
                    should_pow = False
                expr = getattr(sympy.functions, name)(arg, evaluate=False)


        if func_pow and should_pow:
            expr = sympy.Pow(expr, func_pow, evaluate=False)

        return expr
    elif func.LETTER() or func.SYMBOL():
        if func.LETTER(): 
            fname = func.LETTER().getText()
        elif func.SYMBOL():
            fname = func.SYMBOL().getText()[1:]
        fname = str(fname) # can't be unicode
        if func.subexpr():
            subscript = None
            if func.subexpr().expr():                   # subscript is expr
                subscript = convert_expr(func.subexpr().expr())
            else:                                       # subscript is atom
                subscript = convert_atom(func.subexpr().atom())
            subscriptName = StrPrinter().doprint(subscript)
            fname += '_{' + subscriptName + '}'
        input_args = func.args()
        output_args = []
        while input_args.args():                        # handle multiple arguments to function
            output_args.append(convert_expr(input_args.expr()))
            input_args = input_args.args()
        output_args.append(convert_expr(input_args.expr()))
        return sympy.Function(fname)(*output_args)
    elif func.FUNC_INT():
        return handle_integral(func)
    elif func.FUNC_SQRT():
        expr = convert_expr(func.base)
        if func.root:
            r = convert_expr(func.root)
            return sympy.root(expr, r)
        else:
            return sympy.sqrt(expr)
    elif func.FUNC_SUM():
        return handle_sum_or_prod(func, "summation")
    elif func.FUNC_PROD():
        return handle_sum_or_prod(func, "product")
    elif func.FUNC_LIM():
        return handle_limit(func)

def convert_func_arg(arg):
    if hasattr(arg, 'expr'):
        return convert_expr(arg.expr())
    else:
        return convert_mp(arg.mp_nofunc())

def handle_integral(func):
    if func.additive():
        integrand = convert_add(func.additive())
    elif func.frac():
        integrand = convert_frac(func.frac())
    else:
        integrand = 1

    int_var = None
    if func.DIFFERENTIAL():
        int_var = get_differential_var(func.DIFFERENTIAL())
    else:
        for sym in integrand.atoms(sympy.Symbol):
            s = str(sym)
            if len(s) > 1 and s[0] == 'd':
                if s[1] == '\\':
                    int_var = sympy.Symbol(s[2:])
                else:
                    int_var = sympy.Symbol(s[1:])
                int_sym = sym
        if int_var:
            integrand = integrand.subs(int_sym, 1)
        else:
            # Assume dx by default
            int_var = sympy.Symbol('x')

    if func.subexpr():
        if func.subexpr().atom():
            lower = convert_atom(func.subexpr().atom())
        else:
            lower = convert_expr(func.subexpr().expr())
        if func.supexpr().atom():
            upper = convert_atom(func.supexpr().atom())
        else:
            upper = convert_expr(func.supexpr().expr())
        return sympy.Integral(integrand, (int_var, lower, upper))
    else:
        return sympy.Integral(integrand, int_var)

def handle_sum_or_prod(func, name):
    val      = convert_mp(func.mp())
    iter_var = convert_expr(func.subeq().equality().expr(0))
    start    = convert_expr(func.subeq().equality().expr(1))
    if func.supexpr().expr(): # ^{expr}
        end = convert_expr(func.supexpr().expr())
    else: # ^atom
        end = convert_atom(func.supexpr().atom())
        

    if name == "summation":
        return sympy.Sum(val, (iter_var, start, end))
    elif name == "product":
        return sympy.Product(val, (iter_var, start, end))

def handle_limit(func):
    sub = func.limit_sub()
    if sub.LETTER():
        var = sympy.Symbol(sub.LETTER().getText())
    elif sub.SYMBOL():
        var = sympy.Symbol(sub.SYMBOL().getText()[1:])
    else:
        var = sympy.Symbol('x')
    if sub.SUB():
        direction = "-"
    else:
        direction = "+"
    approaching = convert_expr(sub.expr())
    content     = convert_mp(func.mp())
    
    return sympy.Limit(content, var, approaching, direction)

def get_differential_var(d):
    text = get_differential_var_str(d.getText())
    return sympy.Symbol(text)

def get_differential_var_str(text):
    for i in range(1, len(text)):
        c = text[i]
        if not (c == " " or c == "\r" or c == "\n" or c == "\t"):
            idx = i
            break
    text = text[idx:]
    if text[0] == "\\":
        text = text[1:]
    return text

# Helper method for preprocessing
def helper_pre_process(illegal, mathmode):
    split = re.split(illegal, mathmode)
    mathmode = ''
    for s in split[:-1]:
        mathmode += s + r''
    mathmode += split[-1]

    return mathmode

# Gets rid of first charachter
def helper_pre_process_first(illegal, mathmode):
    split = re.findall(illegal, mathmode)
    mainSplit = mathmode.split()

    mathmode = ''
    for word in mainSplit:
        if word in split:
            word = word[1:]
        mathmode += word + r' '

    return mathmode

# Function to return immediet underscore/exponent
def underscore_exp(func):
    func = func.replace('{', '')
    func = func.replace('}', '')
    if (func[0] == '_'):
        func = func.replace('_', '')
        returnObj = ' underscore '
    elif (func[0] == '^'):
        func = func.replace('^', '')
        returnObj = ' to the power of '
    else:
        return False 

    func = func.replace('{', '')
    func = func.replace('}', '')
    returnObj = '<break time = "0.3s"/> '+ returnObj + str(func[0:]) + ' <break time = "0.3s"/>'

    return returnObj

# Gets rids of special charachters that this 
# parser can't handle
def pre_process(mathmode):
    # Gets rid of illegal & commands
    illegalAmpPat = r'(?:&(?!amp;|lt;|gt;|quot;|apos;))'
    # mathmode = helper_pre_process(illegalAmpPat, mathmode)

    # Gets rid of \
    illegalSlask = r'\\\W'
    mathmode = helper_pre_process(illegalSlask, mathmode)
    illegalSlask = r'\\\d'
    mathmode = helper_pre_process_first(illegalSlask, mathmode)

    # Gets rid of . which are not between numbers and thus not decimals
    mathmode = re.sub(r"(\D)\.(\D)", "\\1\\2", mathmode)

    # Gets rid of ,
    # mathmode = mathmode.replace(',', '')

    # Gets rid of ;
    mathmode = mathmode.replace(';', '') 
    mathmode = re.sub(r"\s+"," ", mathmode, flags = re.I)
    return mathmode

def test_sympy(mathmode):
    return process_sympy(mathmode)

def post_process(sympy_text):
    # Gets rid of trailing zeros at the end of a decimal number
    sympy_text = re.sub(r"(\.[0]{0,}[1-9]{1,})[0]{1,}", "\\1", sympy_text)
    # Add pause before and after math
    sympy_text = "<break time = \"0.3s\"/>" + sympy_text + "<break time = \"0.3s\"/>"
    # Overcome the fact that sympy does not support 'plus or minus' or 'minus'
    # This is a terrible hack and must be changed. 
    # It also does not work when quantity/parentheses indicators are on
    sympy_text = sympy_text.replace('times pm times', 'plus or minus')
    sympy_text = sympy_text.replace('plus -', 'minus')
    # Replace fake infinity character
    sympy_text = sympy_text.replace(' oo ', ' infinity ')
    return sympy_text

def run_sympy(mathmode):
    quantity_mode, error_notification = get_config()
    try:
        cleanedMathmode = pre_process(mathmode)
        evaluator = underscore_exp(mathmode)
        if (evaluator == False):
            sympyObj = process_sympy(cleanedMathmode)
            ssmlObj = convert_sympy_ssml((sympyObj), quantity_mode)
            return post_process(ssmlObj)
        return evaluator

    except (RuntimeError, TypeError, NameError, SyntaxError, Exception) as e:
        print(e)
        return error_notification

# Quantity mode helper
def get_quantity_mode(mode):
    quantity_mode = QuantityModes.NO_INDICATOR
    if mode == 'quantity':
        quantity_mode = QuantityModes.QUANTITY
    elif mode == 'quantity_numbered':
        quantity_mode = QuantityModes.QUANTITY_NUMBERED
    elif mode == 'parentheses':
        quantity_mode = QuantityModes.PARENTHESES
    elif mode == 'parentheses_numbered':
        quantity_mode = QuantityModes.PARENTHESES_NUMBERED
    return quantity_mode

# Helper method to get quantity depending on whether it's the default method or config method
def config_quantity_helper(config_content, def_conf):
    type = config_content['QUANTITY_MODE'][def_conf]['TYPE']
    if (type == 'Quantity'):
        quantity_mode = get_quantity_mode(config_content['QUANTITY_MODE'][def_conf]['QUANTITY_METHOD'])
    else:
        quantity_mode = QuantityModes.NO_INDICATOR

    return quantity_mode

# Helper method to get error notification depending on whether it's the default method or config method
def config_error_helper(config_content, def_conf):
    type = config_content['MATH_ERROR'][def_conf]['TYPE']
    if (type == 'Message'):
        error_notification = config_content['MATH_ERROR'][def_conf]['MESSAGE']
    elif (type == 'Break'):
        break_time = config_content['MATH_ERROR'][def_conf]['BREAK_TIME']
        error_notification = '<break time = ' + break_time + '/>'
    else:
        error_notification = ''

    return error_notification

def get_config():
    config_file = open('temporary.yaml')
    config_content = yaml.load(config_file, Loader=yaml.FullLoader)

    # Quantity Modes
    if config_content['QUANTITY_MODE']['CONFIG']['TYPE'] != 'None':
        quantity_mode = config_quantity_helper(config_content,'CONFIG')
    else:
        quantity_mode = config_quantity_helper(config_content,'DEFAULT')

    # Error Message
    if config_content['MATH_ERROR']['CONFIG']['TYPE'] != 'None':
        error_notification = config_error_helper(config_content,'CONFIG')
    else:
        error_notification = config_error_helper(config_content,'DEFAULT')

    return quantity_mode, error_notification