# This file contains information about the different table-
# based file types and methods for validating them
import logging
import re

import pandas as pd
import numpy as np

from .base import DataResource
from api.data_structures import Feature, \
    FeatureSet, \
    Observation, \
    ObservationSet, \
    create_attribute, \
    convert_dtype
from api.serializers.feature_set import FeatureSetSerializer
from api.serializers.observation_set import ObservationSetSerializer

logger = logging.getLogger(__name__)

# acceptable file extensions which give us a
# clue about how to parse thefiles.
TAB_DELIMITED_EXTENSIONS = ['tsv', 'tab', 'bed', 'vcf']
COMMA_DELIMITED_EXTENSIONS = ['csv']
EXCEL_EXTENSIONS = ['xls', 'xlsx']

class ParserNotFoundException(Exception):
    '''
    For raising exceptions when a proper 
    parser cannot be found.
    '''
    pass

class ParseException(Exception):
    '''
    For raising exceptions when the parser
    fails for someon reason.
    '''
    pass

class UnexpectedTypeValidationException(Exception):
    '''
    Raised when a Resource fails to validate but *should have*
    been fine. 

    This would be raised, for instance, when an Operation completes and
    produces some output file, for which we know the type.  In that case,
    a failure to validate would indicate some unexpected error 
    '''
    pass


# Some error messages:
PARSE_ERROR = ('There was an unexpected problem when'
    ' parsing and validating the file.')

PARSER_NOT_FOUND_ERROR = ('Could not find an appropriate parser'
    ' for the resource.  Please check the instructions.')

NON_NUMERIC_ERROR = 'The following columns contained non-numeric entries: {cols}'

NON_INTEGER_ERROR = 'The following columns contained non-integer entries: {cols}'

TRIVIAL_TABLE_ERROR = ('The file contained only a single column'
    ' which provided an index.  No data was provided in additional columns.')

BED_FORMAT_ERROR = ('When parsing the BED file, we detected issues with'
    ' column(s): {cols}. Note that BED files must NOT have column headers and can'
    ' contain only integers in the second and third columns, which correspond'
    ' to the start and end of a genomic location.  Please check your entries and'
    ' ensure your file does not have a header line.')

NUMBERED_COLUMN_NAMES_ERROR = ('All the column names were numbers.  Often this is due'
    ' to a missing column header.  In that case, there will be a missing row in'
    ' your table. If you named your columns with numbers, please change them'
    ' to something else to avoid incorrect parsing of the file.')

NUMBERED_ROW_NAMES_ERROR = ('All the row names were numbers.  We use the first column'
    ' to uniquely identify the rows for filtering purposes. If you named your rows with'
    ' numbers, please change them to something else (e.g. add "x" to the beginning'
    ' to avoid incorrect parsing of the file.')

NONUNIQUE_ROW_NAMES_ERROR = ('Your row names were not unique, which could cause'
    ' unexpected behavior.')

MISSING_HEADER_WARNING = ('One of your column names matched the values in the'
    ' corresponding problem.  This is not an error, but may indicate that a'
    ' proper header line was missing.  Please check to ensure the file was'
    ' parsed correctly.')

EMPTY_TABLE_ERROR = ('The parsed table was empty. If you are trying to'
    ' import an Excel spreadsheet, please ensure that the data is contained'
    ' in the first sheet of the workbook.')

def col_str_formatter(x):
    '''
    x is a tuple with the column number
    and column name
    '''
    return '%s (column %d)' % (x[0],x[1])


class TableResource(DataResource):
    '''
    The `TableResource` is the most generic form of a delimited file.  Any
    type of data that can be represented as rows and columns.

    This or any of the more specific subclasses can be contained in files
    saved in CSV, TSV, or Excel (xls/xlsx) format.  If in Excel format, the 
    data of interest must reside in the first sheet of the workbook.

    Special tab-delimited files like BED or VCF files are recognized by
    their canonical extension (e.g. ".bed" or ".vcf").

    Note that unless you create a "specialized" implementation (e.g. like
    for a BED file), then we assume you have features as rows and observables
    as columns.
    '''

    def __init__(self):
        self.table = None

    @staticmethod
    def get_reader(resource_path):
        '''
        By using the file extension, we infer the delimiter
        Returns a pandas "reader" (e.g. `read_csv` or `read_table`)
        '''
        file_extension = resource_path.split('.')[-1].lower()

        if file_extension in COMMA_DELIMITED_EXTENSIONS:
            return pd.read_csv
        elif file_extension in TAB_DELIMITED_EXTENSIONS:
            return pd.read_table
        elif file_extension in EXCEL_EXTENSIONS:
            return pd.read_excel
        else:
            logger.error('Could not infer the file format from the file'
            ' extension of {ext}.  Full resource path was {path}'.format(
                ext = file_extension,
                path = resource_path
            ))
            return None

    @staticmethod
    def index_all_numbers(names):
        '''
        Works for both row and column indexes.  Returns
        True if all the index labels are numbers.  
        '''
        if all([re.match('\d+', str(x)) for x in names]):
            return True
        else:
            return False

    def read_resource(self, resource_path):
        '''
        One common spot to define how the file is read
        '''
        reader = TableResource.get_reader(resource_path)
        if reader is None:
            raise ParserNotFoundException('')
        else:
            try:
                # read the table using the appropriate parser:
                self.table = reader(resource_path, index_col=0, comment='#')
            except Exception as ex:
                logger.error('Could not use {reader} to parse the file'
                ' at {path}'.format(
                    reader = reader,
                    path = resource_path
                ))     
                raise ParseException('')

    def validate_type(self, resource_path):
        '''
        In this base method, we determine attempt to parse the file.
        If there are no restrictions on content, succesful parsing
        of the file is good enough.

        More specific constraints on the file content are handled in child
        classes.  This method, however, fills in the `self.table` member
        which is then accessible to children.
        '''
        try:
            self.read_resource(resource_path)
            if self.table.shape == (0,0):
                return (False, EMPTY_TABLE_ERROR )

            if self.table.shape[1] == 0:
                return (False, TRIVIAL_TABLE_ERROR)

            # check if all the column names are numbers-- which would USUALLY
            # indicate a missing header
            columns_all_numbers = TableResource.index_all_numbers(self.table.columns)
            if columns_all_numbers:
                return (False, NUMBERED_COLUMN_NAMES_ERROR)

            # check if all the rownames are numbers, which would usually
            # indicate missing row names (i.e. a column of data is read
            # as the index)
            rows_all_numbers = TableResource.index_all_numbers(self.table.index)

            if rows_all_numbers:
                return (False, NUMBERED_ROW_NAMES_ERROR)

            # check for duplicate row names
            if self.table.index.has_duplicates:
                return (False, NONUNIQUE_ROW_NAMES_ERROR)
            return (True, None)

        except ParserNotFoundException as ex:
            return (False, PARSER_NOT_FOUND_ERROR)

        except ParseException as ex:
            return (False, PARSE_ERROR)
     

    def get_preview(self, resource_path):
        '''
        Returns a dict of the table contents

        Note that we don't use the Pandas to_json() method
        since it's a bit verbose.
        '''
        try:
            self.read_resource(resource_path)
            table_head = self.table.head()
            j = {}
            j['columns'] = table_head.columns.tolist()
            j['rows'] = table_head.index.tolist()
            j['values'] = table_head.values.tolist()
            return j

        # for these first two exceptions, we already have logged
        # any problems when we called the `read_resource` method
        except ParserNotFoundException as ex:
            return {
                'error': 
                'Parser for the resource not found.'
            }

        except ParseException as ex:
            return {
                'error': 
                'Parser could not read the resource.'
            }
        
        # catch any other types of exceptions that we did not anticipate.
        except Exception as ex:
            logger.error('An unexpected error occurred when preparing'
                'a resource preview for the resource at {path}'.format(
                    path=resource_path
                ))
            return {
                'error': 
                'An unexpected error occurred.'
            }

    def extract_metadata(self, resource_path, parent_op_pk=None):
        '''
        This method extracts metadata from the Resource in question and 
        saves it to the database.

        In the case of new Resources being added, the `parent_op` is None
        since no MEV-based analyses were responsible for the creation of the 
        Resource.  If the Resource is created by some MEV-based analysis,
        the primary-key for that ExecutedOperation will be passed.

        '''
        logger.info('Extracting metadata from resource with path ({path}).'.format(
            path = resource_path
        ))

        # If the self.table field was not already filled, we need to 
        # read the data
        if self.table is None:
            logger.info('Resource with path ({path}) was not '
                'previously parsed.  Do that now.'.format(
                    path=resource_path
                )
            )
            is_valid, message = self.validate_type(resource_path)
            if not is_valid:
                raise UnexpectedTypeValidationException(message)

        # now we have a table loaded at self.table.  

        # call the super method to initialize the self.metadata
        # dictionary
        super().setup_metadata()

        # now add the information to self.metadata:
        if parent_op_pk:
            self.metadata[DataResource.PARENT_OP] = parent_op_pk



class Matrix(TableResource):
    '''
    A `Matrix` is a delimited table-based file that has only numeric types.
    These types can be mixed, like floats and integers
    '''

    # looking for integers OR floats.  Both are acceptable  
    TARGET_PATTERN = '(float|int)\d{0,2}'

    def check_column_types(self, target_pattern):
        '''
        Checks each column against a specific numpy/pandas dtype.
        The specific dtype comes from the class member.
        '''
        problem_columns = []
        for i,col in enumerate(self.table.dtypes):
            if not re.match(target_pattern, str(col)):
                colname = self.table.columns[i]
                problem_columns.append(
                    (colname, i+1)
                )
        return problem_columns


    def validate_type(self, resource_path):
        is_valid, error_msg = super().validate_type(resource_path)
        if not is_valid:
            return (False, error_msg)

        # was able to at least open/parse the file.
        # now check for numeric types
        problem_columns = self.check_column_types(Matrix.TARGET_PATTERN)

        if len(problem_columns) > 0:
            col_str = ', '.join([col_str_formatter(x) for x in problem_columns])
            error_message = NON_NUMERIC_ERROR.format(cols=col_str)
            return (False, error_message)

        return (True, None)

    def extract_metadata(self, resource_path, parent_op_pk=None):

        super().extract_metadata(resource_path, parent_op_pk)

        # the FeatureSet comes from the rows:
        f_set = FeatureSet([Feature(x) for x in self.table.index])
        self.metadata[DataResource.FEATURE_SET] = FeatureSetSerializer(f_set).data

        # the ObservationSet comes from the cols:
        o_set = ObservationSet([Observation(x) for x in self.table.columns])
        self.metadata[DataResource.OBSERVATION_SET] = ObservationSetSerializer(o_set).data
        return self.metadata


class IntegerMatrix(Matrix):
    '''
    An `IntegerMatrix` further specializes the `Matrix`
    to admit only integers.
    '''
    # looking for only integers. 
    TARGET_PATTERN = 'int\d{0,2}'

    def validate_type(self, resource_path):

        # first check that it has all numeric types.  If that fails
        # immediately return--
        is_valid, error_message = super().validate_type(resource_path)
        if not is_valid:
            return (False, error_message)

        # was valid for numeric types.  Now check for integer
        problem_columns = self.check_column_types(IntegerMatrix.TARGET_PATTERN)
        if len(problem_columns) > 0:

            # one problem with pandas is that NaN values cause a column
            # to be parsed as a float, even if all other values in the 
            # column are integers.  We can do a secondary check, however, 
            # to see if the remaining values (non-NaN) are basically
            # integers.  We check if the numbers look like "2.0".
            # If that is the case, we remove that column from the 
            # "problem columns".  
            for i,c in enumerate(problem_columns):
                # recall c is a tuple of (colname, col number)
                if all([
                        re.match('\d+\.0', str(x)) 
                        for x in self.table[c[0]].dropna()]):
                    problem_columns.pop(i)

            # if there are still any remaining problematic cols,
            # we now issue an error
            if len(problem_columns) > 0:
                col_str = ', '.join([col_str_formatter(x) for x in problem_columns])
                error_message = NON_INTEGER_ERROR.format(cols=col_str)
                return (False, error_message)
            
        return (True, None)


class ElementTable(TableResource):
    '''
    An ElementTable captures common behavior of tables which
    annotate Observations (AnnotationTable) or Features (FeatureTable)

    It's effectively an abstract class-- 
    '''

    def validate_type(self, resource_path):

        # check that file can be parsed:
        is_valid, error_message = super().validate_type(resource_path)
        if not is_valid:
            return (False, error_message)
        
        # check that the file is "useful" in that it has
        # more than one column.  It's not REALLY an error, but it does not 
        # provide any information.  This can also be caught earlier, but
        # we provide it here just as a secondary guard.
        if self.table.shape[1] == 0:
            return (False, TRIVIAL_TABLE_ERROR)
        return (True, None)

    def prep_metadata(self, element_class):
        '''
        When we extract the metadata from an ElementTable, we 
        expect the Element instances (Observations or Features) 
        to be contained in the rows.  

        Additional columns specify attributes which we incorporate.

        The `element_class` arg is a class which implements the specific
        type we want (i.e. Observation or Feature)
        '''
        # Go through the columns and find out the primitive types
        # for each column/covariate.
        # Note that we can't determine specific types (e.g. bounded integers)
        # from general annotations.  We basically allow floats, integers, and
        # "other" types, which get converted to strings.
        type_dict = {}
        for c in self.table.dtypes.index:
            # the convert_dtype function takes the native pandas dtype
            # and returns an attribute "type" that MEV understands.
            type_dict[c] = convert_dtype(str(self.table.dtypes[c]))

        element_list = []
        for id, row_series in self.table.iterrows():
            d = row_series.to_dict()
            attr_dict = {}
            for key, val in d.items():
                attr = create_attribute(key,
                    {
                        'attribute_type': type_dict[key],
                        'value': val
                    }
                )
                attr_dict[key] = attr
            element_list.append(element_class(id, attr_dict))
        return element_list


class AnnotationTable(ElementTable):
    '''
    An `AnnotationTable` is a special type of table that will be responsible
    for annotating Observations/samples (e.g. adding sample names and 
    associated attributes like experimental group or other covariates)

    The first column will give the sample names and the remaining columns will
    each individually represent different covariates associated with that sample.
    '''
    def validate_type(self, resource_path):

        # check that file can be parsed:
        is_valid, error_message = super().validate_type(resource_path)
        if not is_valid:
            return (False, error_message)

        # it is hard to check for proper headers for annotation
        # files since they have relatively free format.  However,
        # if the column name matches any values in its column, the
        # annotation was likely missing a header line.  For example,
        # >>> df
        # SW1_Control   CTRL
        # 0  SW2_Control   CTRL
        # 1  SW3_Control   CTRL
        # 2  SW4_Treated  TREAT
        # 3  SW5_Treated  TREAT
        # 4  SW6_Treated  TREAT
        #
        # Here, "CTRL" becomes the header, but it's clearly 
        # just due to a missing header.  We don't issue an 
        # error, but we do warn the user by adding a comment.
        flagged_columns = []
        for c in self.table.columns:
            if np.sum(c == self.table[c]) > 0:
                flagged_columns.append(c)
        if len(flagged_columns) > 0:
            return (True, MISSING_HEADER_WARNING)

        return (True, None)

    def extract_metadata(self, resource_path, parent_op_pk=None):
        '''
        When we extract the metadata from an AnnotationTable, we 
        expect the Observation instances to be the rows.  

        Additional columns specify attributes of each Observation,
        which we incorporate
        '''
        super().extract_metadata(resource_path, parent_op_pk)

        observation_list = super().prep_metadata(Observation)
        o_set = ObservationSet(observation_list)
        self.metadata[DataResource.OBSERVATION_SET] = ObservationSetSerializer(o_set).data
        return self.metadata


class FeatureTable(ElementTable):
    '''
    A `FeatureTable` is a type of table that has aggregate information about
    the features, but does not have any "observations" in the columns.  An example
    would be the results of a differential expression analysis.  Each row corresponds
    to a gene (feature) and the columns are information about that gene (such as p-value).

    Another example could be a table of metadata about genes (e.g. pathways or perhaps a 
    mapping to a different gene identifier).

    The first column will give the feature/gene identifiers and the remaining columns will
    have information about that gene
    '''

    def extract_metadata(self, resource_path, parent_op_pk=None):
        '''
        When we extract the metadata from a FeatureTable, we 
        expect the Feature instances to be the rows.  

        Additional columns specify attributes of each Feature,
        which we incorporate
        '''
        super().extract_metadata(resource_path, parent_op_pk)

        feature_list = super().prep_metadata(Feature)
        f_set = FeatureSet(feature_list)
        self.metadata[DataResource.FEATURE_SET] = FeatureSetSerializer(f_set).data
        return self.metadata


class BEDFile(TableResource):
    '''
    A file format that corresponds to the BED format.  This is
    the minimal BED format, which has:

    - chromosome
    - start position
    - end position

    Additional columns are ignored.

    By default, BED files do NOT contain headers and we enforce that here.
    '''
    def validate_type(self, resource_path):
        reader = TableResource.get_reader(resource_path)

        # if the BED file has a header, the reader below will incorporate
        # that into the columns and the 2nd and 3rd columns will no longer have
        # the proper integer type.
        table = pd.read_table(resource_path, 
            names=['chrom','start','stop'],
            usecols=[0,1,2])
        start_col_int = re.match('int\d{0,2}', str(table['start'].dtype))
        stop_col_int = re.match('int\d{0,2}', str(table['stop'].dtype))
        if start_col_int and stop_col_int:
            return (True, None)
        else:
            problem_columns = []
            if start_col_int is None:
                problem_columns.append(2)
            if stop_col_int is None:
                problem_columns.append(3)

            cols = ','.join([str(x) for x in problem_columns])
            error_message = BED_FORMAT_ERROR.format(cols=cols)
            return (False, error_message)

    def extract_metadata(self, resource_path, parent_op_pk=None):
        super().extract_metadata(resource_path, parent_op_pk)
        return self.metadata
