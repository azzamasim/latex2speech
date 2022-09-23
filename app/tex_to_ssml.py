import yaml
from doc_preprocess import doc_preprocess
from modify_xml import run_xml_modify
from doc_cleanup import cleanxml_string

# Internal classes
from conversion_db import ConversionDB
from conversion_parser import ConversionParser
basicMathFilename = '../Documentation/sample_input_files/basicMath.tex'
testFilename = "./static/tex_files/latex_test.tex"
def configure_temp_yaml():
    with open('app_config.yaml') as f:
        doc = yaml.load(f)

    # Can configure options here

    with open('temporary.yaml', 'w') as f:
        yaml.dump(doc, f)

# Pass off to parser
def start_conversion(contents):
    run_xml_modify()

    # Create database/parser
    db_source = open('static/xml/output.xml')
    db = ConversionDB(db_source.read())
    parser = ConversionParser(db)

    parsed_contents = parser.parse(contents)

    return parsed_contents


################################ Parsing begins here ################################

configure_temp_yaml()

## input sample latex file
tex_file = (open(testFilename, 'r'))  
tex_file_text = tex_file.read()
# print(tex_file_text)

## pre-process tex file
# doc_preprocess(basicMathFilename)
#! this function expects only the file name, pass that for now
# and later modify the function so that it works with the actual file

parsed_contents = start_conversion(tex_file_text)
# this is the parsed ssml object

#post processing/clean up
parsed_contents = cleanxml_string(parsed_contents)

print(parsed_contents)
#Just surround this text with <speak> tags and you have ssml


tex_file.close()
