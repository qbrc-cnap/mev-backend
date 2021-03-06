import os
import uuid
import json
import logging

from django.conf import settings

from api.models import Resource, ResourceMetadata
from api.serializers.resource_metadata import ResourceMetadataSerializer
from .basic_utils import make_local_directory, \
    move_resource, \
    copy_local_resource, \
    get_filesize
from resource_types import get_preview, \
    PARENT_OP_KEY, \
    OBSERVATION_SET_KEY, \
    FEATURE_SET_KEY

logger = logging.getLogger(__name__)

def check_for_resource_operations(resource_instance):
    '''
    To prevent deleting critical resources, we check to see if a
    `Resource` instance has been used for any operations within a
    `Workspace`.  If it has, return True.  Otherwise return False.
    '''
    pass

def create_resource_from_upload(filepath, 
    filename, 
    resource_type, 
    is_public,
    is_local,
    owner):
    '''
    Creates and returns a Resource instance.
    `filepath` is a path to the file-based resource.  Could be local or in bucket-based
    storage.
    `filename` is a string, extracted from the name of the uploaded file.
    `resource_type` is one of the acceptable resource type identifiers.  
       - At this point, it's just a "requested" resource type and has not
         been verified.
       - Note that even though we fill the resource_type field in the dict 
         below, we do not end up setting that member on the database object.
         See the `ResourceSerializer.create` method. 
    `is_public` is a boolean.
    `owner` is an instance of our user model
    '''
    # get the size of the file:
    size = get_filesize(filepath, is_local)

    # create a serialized representation so we can use the validation
    # contained there.  Note that since we are creating a new Resource
    # we have NOT validated it.
    d = {'owner_email': owner.email,
        'path': filepath,
        'name': filename,
        'resource_type': resource_type,
        'is_public': is_public,
        'is_local': is_local,
        'size': size
    }

    from api.serializers.resource import ResourceSerializer
    rs = ResourceSerializer(data=d)

    # values were checked prior to this, but we enforce this again
    if rs.is_valid(raise_exception=True):
        r = rs.save()
        return r

def set_resource_to_validation_status(resource_instance):
    '''
    Function created to temporarily "disable"
    a Resource while it is pending verification of the
    type.  
    '''
    resource_instance.status = Resource.VALIDATING
    resource_instance.is_active = False
    resource_instance.save()


def move_resource_to_final_location(resource_instance):
    '''
    Given a `Resource` instance (validated), create an appropriate
    space for the resource
    '''

    # create a final file location by concatenating the
    # resource UUID and the file's "human readable" name
    owner_uuid = str(resource_instance.owner.user_uuid)
    basename = '{uuid}.{name}'.format(
        uuid=resource_instance.pk, 
        name=resource_instance.name
    )

    if resource_instance.is_local:
        user_dir = os.path.join(
            settings.USER_STORAGE_DIR,
            owner_uuid
        )
        if not os.path.exists(user_dir):
            # in the unlikely event the directory cannot be made
            # this function will raise an exception, which will be
            # caught in the calling function
            make_local_directory(user_dir)

        final_path = os.path.join(
            user_dir,
            basename
        )
        # if the file cannot be moved, the following function may raise an exception.
        # We catch in the calling function
        final_path = move_resource(resource_instance.path, final_path)
        return final_path

    else:
        # TODO: implement
        raise NotImplementedError('Implement logic for remote resource')


def copy_resource_to_workspace(unattached_resource, workspace):
    '''
    This function handles the copy of an existing (and validated)
    Resource when it is added to a Workspace.

    Note that it only creates the appropriate
    database object- in the case of large files, we
    do not want to copy those.
    '''
    logger.info('Adding resource ({resource}) to'
        ' workspace ({workspace}).'.format(
            workspace = str(workspace),
            resource = str(unattached_resource)
        )
    )  

    metadata_queryset = ResourceMetadata.objects.filter(resource=unattached_resource)

    # we need to create a new Resource with the Workspace 
    # field filled appropriately.  Note that this method of "resetting"
    # the primary key by setting it to None creates an effective copy
    # of the original resource. We then alter the path field and save.
    r = unattached_resource
    r.pk = None
    r.workspace = workspace
    r.is_public = False # when we copy to a workspace, set private
    r.save()

    # also need to copy the metadata
    if len(metadata_queryset) == 0:
        logger.error('Was trying to add Resource {r_uuid}'
            ' to a Workspace ({w_uuid}), but there was no'
            ' metadata associated with it'.format(
                r_uuid = unattached_resource.pk,
                w_uuid = workspace.pk
            ))
    elif len(metadata_queryset) > 1:
        logger.error('Was trying to add Resource {r_uuid}'
            ' to a Workspace ({w_uuid}), but there were'
            ' multiple metadata instances associated with it'.format(
                r_uuid = unattached_resource.pk,
                w_uuid = workspace.pk
            ))
    else:
        metadata = metadata_queryset[0]
        metadata.pk = None
        metadata.resource = r
        metadata.save()

    return r

def check_for_shared_resource_file(resource_instance):
    '''
    When a Resource deletion is requested, we need to be a bit careful about
    deleting database records, the underlying files, or both.

    Now we need to check if any OTHER Resource instances 
    reference the same path.  If there are, we only delete
    the database record, NOT the file.  If this is the only
    database record referring to the file, we can delete both
    the record and the file.

    Returns True if multiple Resources reference the same file.
    '''
    path = resource_instance.path
    if len(path) > 0: # in case the path was empty somehow.
        all_resources_with_path = Resource.objects.filter(path=path)
        if len(all_resources_with_path) == 0:
            logger.error('Unexpected exception when'
            ' attempting to delete a Resource instance. Despite'
            ' filtering with the path member, could not locate'
            ' any Resource instances referencing that same path.')
            raise Exception('No Resource found')

        elif len(all_resources_with_path) == 1: 
            # only one Resource record references the path.  
            # Double-check that it's the same as the current
            # instance we're considering 
            r = all_resources_with_path[0]
            if r.pk != resource_instance.pk:
                logger.error('Unexpected exception when'
                ' attempting to delete a Resource instance.  Did not "re-find"'
                ' the Resource instance we used to search with.')
                raise Exception('Database inconsistency!')
            else:
                # consistency check worked.  Only a singe Resource instance
                # references the file.  Delete both the file AND the record.
                return False
        else:
            return True


def get_resource_preview(resource_instance):
    '''
    Returns a "preview" of the resource_instance in JSON-format.

    Only valid for certain resource types and assumes
    that the resource is active. 
    '''
    logger.info('Retrieving preview for resource: {resource}.'.format(
        resource=resource_instance
    ))

    if not resource_instance.resource_type:
        logger.info('No resource type was known for resource: {resource}.'.format(
            resource = resource_instance
        ))
        return {
            'info': 'No preview available since the resource'
            ' type was not set.'
        }

    return get_preview(resource_instance.path, resource_instance.resource_type)


def add_metadata_to_resource(resource, metadata):
    metadata['resource'] = resource.pk
    try:
        rm = ResourceMetadata.objects.get(resource=resource)
        rms = ResourceMetadataSerializer(rm, data=metadata)
    except ResourceMetadata.DoesNotExist:
        rms = ResourceMetadataSerializer(data=metadata)
    if rms.is_valid(raise_exception=True):
        rms.save()


def handle_valid_resource(resource, resource_class_instance, requested_resource_type):
    '''
    Once a Resource has been successfully validated, this function does some
    final operations such as moving the file and extracting metadata.
    '''
    resource.status = Resource.READY

    # if the existing resource.resource_type field was not set (null/None)
    # then that means it has never passed validation (e.g. a new file).
    # Move it to its final location
    if not resource.resource_type:
        try:
            final_path = move_resource_to_final_location(resource)
            resource.path = final_path
        except Exception as ex:
            # a variety of exceptions can be raised due to failure
            # to create directories/files.  Catch them all here and
            # set the resource to be inactive.
            logger.error('An exception was raised following'
            ' successful validation of reosurce {resource}.'
            ' Exception trace is {ex}'.format(
                ex=ex,
                resource = resource
            ))

    resource.resource_type = requested_resource_type

    # since the resource was valid, we can also fill-in the metadata
    metadata = resource_class_instance.extract_metadata(resource.path)
    add_metadata_to_resource(resource, metadata)