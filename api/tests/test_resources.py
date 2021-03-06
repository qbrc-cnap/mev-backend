import uuid
import os
import json
import unittest.mock as mock

from django.urls import reverse
from django.core.exceptions import ImproperlyConfigured
from rest_framework import status
from rest_framework.exceptions import ValidationError

from api.models import Resource, Workspace
from resource_types import DATABASE_RESOURCE_TYPES
from api.tests.base import BaseAPITestCase
from api.tests import test_settings


class ResourceListTests(BaseAPITestCase):

    def setUp(self):

        self.url = reverse('resource-list')
        self.establish_clients()

    def test_list_resource_requires_auth(self):
        """
        Test that general requests to the endpoint generate 401
        """
        response = self.regular_client.get(self.url)
        self.assertTrue((response.status_code == status.HTTP_401_UNAUTHORIZED) 
        | (response.status_code == status.HTTP_403_FORBIDDEN))

    def test_admin_can_list_resource(self):
        """
        Test that admins can see all Resources.  Checks by comparing
        the pk (a UUID) between the database instances and those in the response.
        """
        response = self.authenticated_admin_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        all_known_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        received_resource_uuids = set([x['id'] for x in response.data])
        self.assertEqual(all_known_resource_uuids, received_resource_uuids)


    @mock.patch('api.serializers.resource.api_tasks')
    @mock.patch('api.serializers.resource.set_resource_to_validation_status')
    def test_admin_can_create_resource(self, 
        mock_set_resource_to_validation_status,
        mock_api_tasks):
        """
        Test that admins can create a Resource and that the proper validation
        methods are called.
        """
        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        payload = {
            'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type':'MTX'
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # get current instances:
        current_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        difference_set = current_resource_uuids.difference(initial_resource_uuids)
        self.assertEqual(len(difference_set), 1)

        # check that the proper validation methods were called
        mock_set_resource_to_validation_status.assert_called()
        mock_api_tasks.validate_resource.delay.assert_called()

        # check that the resource has the proper members set:
        r = Resource.objects.get(pk=list(difference_set)[0])
        self.assertFalse(r.is_active)
        # should be False since it was not explicitly set to True
        self.assertFalse(r.is_public)
        self.assertIsNone(r.resource_type)


    @mock.patch('api.serializers.resource.api_tasks')
    @mock.patch('api.serializers.resource.set_resource_to_validation_status')
    def test_missing_owner_in_admin_resource_request_fails(self, 
        mock_set_resource_to_validation_status,
        mock_api_tasks):
        """
        Test that admins must specify an owner_email field in their request
        to create a Resource directly via the API
        """
        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        payload = {
            'name': 'some_file.txt',
            'resource_type':'MTX'
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        current_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        difference_set = current_resource_uuids.difference(initial_resource_uuids)
        self.assertEqual(len(difference_set), 0)

    def test_bad_admin_request_fails(self):
        """
        Test that even admins must specify a valid resource_type.
        The type given below is junk.
        """
        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        # payload is missing the resource_type key
        payload = {
            'owner_email': self.regular_user_1.email,
            'resource_type': 'ASDFADSFASDFASFSD',
            'name': 'some_file.txt',
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        current_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        difference_set = current_resource_uuids.difference(initial_resource_uuids)
        self.assertEqual(len(difference_set), 0)

    def test_invalid_resource_type_raises_exception(self):
        """
        Test that a bad resource_type specification generates
        an error
        """
        payload = {
            'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type': 'foo'
        }

        response = self.authenticated_admin_client.post(
            self.url, data=payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


    def test_null_resource_type_is_valid(self):
        """
        Test that an explicit null resource_type is OK.
        Users will eventually have to set their own type
        """
        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        payload = {
            'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type': None
        }

        response = self.authenticated_admin_client.post(
            self.url, data=payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # check that we have a new Resource in the database:
        current_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        difference_set = current_resource_uuids.difference(initial_resource_uuids)
        self.assertEqual(len(difference_set), 1)

    @mock.patch('api.serializers.resource.api_tasks')
    @mock.patch('api.serializers.resource.set_resource_to_validation_status')
    def test_admin_can_create_resource_assoc_with_workspace(self,
        mock_set_resource_to_validation_status,
        mock_api_tasks):
        """
        Test that giving a valid workspace properly associates
        the resource that was created
        """

        # get the user's workspaces and just take the first
        user_workspaces = Workspace.objects.filter(owner=self.regular_user_1)
        workspace = user_workspaces[0]

        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        payload = {
            'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type': 'MTX',
            'workspace': workspace.pk
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # get current instances:
        current_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        difference_set = current_resource_uuids.difference(initial_resource_uuids)
        self.assertEqual(len(difference_set), 1)

        # check that the proper validation methods were called
        mock_set_resource_to_validation_status.assert_called()
        mock_api_tasks.validate_resource.delay.assert_called()

        # check that the resource has the proper members set:
        r = Resource.objects.get(pk=list(difference_set)[0])
        self.assertFalse(r.is_active)
        # should be False since it was not explicitly set to True
        self.assertFalse(r.is_public)
        self.assertIsNone(r.resource_type)

    def test_bad_workspace_provided(self):
        """
        Test that giving a bad UUID for the Workspace fails the request.
        """

        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        payload = {
            'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type': 'MTX',
            'workspace': str(uuid.uuid4())
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


    def test_assigning_to_other_users_workspace_fails(self):
        """
        Test an admin creating a new Resource (assigning to reg user 1)
        and giving a valid Workspace, but that workspace belongs
        to a different user than reg user 1
        """

        # get the workspaces for user 2
        other_user_workspaces = Workspace.objects.filter(owner=self.regular_user_2)
        workspace = other_user_workspaces[0]

        payload = {
            'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type': 'MTX',
            'workspace': workspace.pk
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


    def test_admin_sending_bad_email_raises_error(self):
        """
        Test that admins providing a bad email (a user who is not in the db) raises 400
        """
        # get all initial instances before anything happens:
        initial_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])

        payload = {'owner_email': test_settings.JUNK_EMAIL,
            'name': 'some_file.txt',
            'resource_type': 'MTX'
        }
        response = self.authenticated_admin_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # get current instances to check none were created:
        current_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        difference_set = current_resource_uuids.difference(initial_resource_uuids)
        self.assertEqual(len(difference_set), 0)

    def test_regular_user_post_raises_error(self):
        """
        Test that regular users cannot post to this endpoint (i.e. to
        create a Resource).  All Resource creation should be handled by
        the upload methods or be initiated by an admin.
        """
        payload = {'owner_email': self.regular_user_1.email,
            'name': 'some_file.txt',
            'resource_type': 'MTX'
        }
        response = self.authenticated_regular_client.post(self.url, data=payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


    def test_users_can_list_resource(self):
        """
        Test that regular users can list ONLY their own resources
        """
        response = self.authenticated_regular_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        all_known_resource_uuids = set([str(x.pk) for x in Resource.objects.all()])
        personal_resources = Resource.objects.filter(owner=self.regular_user_1)
        personal_resource_uuids = set([str(x.pk) for x in personal_resources])
        received_resource_uuids = set([x['id'] for x in response.data])

        # checks that the test below is not trivial.  i.e. there are Resources owned by OTHER users
        self.assertTrue(len(all_known_resource_uuids.difference(personal_resource_uuids)) > 0)

        # checks that they only get their own resources (by checking UUID)
        self.assertEqual(personal_resource_uuids, received_resource_uuids)



class ResourceDetailTests(BaseAPITestCase):

    def setUp(self):

        self.establish_clients()

        # get an example from the database:
        regular_user_resources = Resource.objects.filter(
            owner=self.regular_user_1,
        )
        if len(regular_user_resources) == 0:
            msg = '''
                Testing not setup correctly.  Please ensure that there is at least one
                Resource instance for the user {user}
            '''.format(user=self.regular_user_1)
            raise ImproperlyConfigured(msg)

        active_resources = []
        inactive_resources = []
        for r in regular_user_resources:
            if r.is_active:
                active_resources.append(r)
            else:
                inactive_resources.append(r)
        if len(active_resources) == 0:
            raise ImproperlyConfigured('Need at least one active resource.')
        if len(inactive_resources) == 0:
            raise ImproperlyConfigured('Need at least one INactive resource.')
        # grab the first:
        self.active_resource = active_resources[0]
        self.inactive_resource = inactive_resources[0]


        # we need some Resources that are associated with a Workspace and 
        # some that are not.
        unassociated_resources = []
        workspace_resources = []
        for r in regular_user_resources:
            if r.workspace:
                workspace_resources.append(r)
            else:
                unassociated_resources.append(r)
        
        # need an active AND unattached resource
        active_and_unattached = set(
                [x.pk for x in active_resources]
            ).intersection(set(
                [x.pk for x in unassociated_resources]
            )
        )
        if len(active_and_unattached) == 0:
            raise ImproperlyConfigured('Need at least one active and unattached'
                ' Resource to run this test.'
        )

        self.regular_user_unattached_resource = unassociated_resources[0]
        self.regular_user_workspace_resource = workspace_resources[0]
        self.populated_workspace = self.regular_user_workspace_resource.workspace
        active_unattached_resource_pk = list(active_and_unattached)[0]
        self.regular_user_active_unattached_resource = Resource.objects.get(
            pk=active_unattached_resource_pk)

        self.url_for_unattached = reverse(
            'resource-detail', 
            kwargs={'pk':self.regular_user_unattached_resource.pk}
        )
        self.url_for_active_unattached = reverse(
            'resource-detail', 
            kwargs={'pk':self.regular_user_active_unattached_resource.pk}
        )
        self.url_for_workspace_resource = reverse(
            'resource-detail', 
            kwargs={'pk':self.regular_user_workspace_resource.pk}
        )
        self.url_for_active_resource = reverse(
            'resource-detail', 
            kwargs={'pk':self.active_resource.pk}
        )
        self.url_for_inactive_resource = reverse(
            'resource-detail', 
            kwargs={'pk':self.inactive_resource.pk}
        )

    def test_resource_detail_requires_auth(self):
        """
        Test that general requests to the endpoint generate 401
        """
        response = self.regular_client.get(self.url_for_unattached)
        self.assertTrue((response.status_code == status.HTTP_401_UNAUTHORIZED) 
        | (response.status_code == status.HTTP_403_FORBIDDEN))
        response = self.regular_client.get(self.url_for_workspace_resource)
        self.assertTrue((response.status_code == status.HTTP_401_UNAUTHORIZED) 
        | (response.status_code == status.HTTP_403_FORBIDDEN))

    def test_admin_can_view_resource_detail(self):
        """
        Test that admins can view the Workpace detail for anyone
        """
        response = self.authenticated_admin_client.get(self.url_for_unattached)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(self.regular_user_unattached_resource.pk), response.data['id'])

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    def test_admin_can_delete_resource(self, mock_api_tasks, mock_file_check):
        """
        Test that admin users can delete an unattached Resource
        """
        mock_file_check.return_value = False
        response = self.authenticated_admin_client.delete(self.url_for_active_unattached)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_api_tasks.delete_file.delay.assert_called()
        with self.assertRaises(Resource.DoesNotExist):
            Resource.objects.get(pk=self.regular_user_active_unattached_resource.pk)

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    @mock.patch('api.views.resource_views.check_for_resource_operations')
    def test_admin_can_delete_unused_resource(self,
            mock_check_for_resource_operations,
            mock_api_tasks,
            mock_file_check):
        """
        Test that admin users can delete a workspace-associated Resource if it 
        has not been used.
        """
        mock_file_check.return_value = False
        mock_check_for_resource_operations.return_value = False
        response = self.authenticated_admin_client.delete(self.url_for_workspace_resource)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_api_tasks.delete_file.delay.assert_called()
        with self.assertRaises(Resource.DoesNotExist):
            Resource.objects.get(pk=self.regular_user_workspace_resource.pk)

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    @mock.patch('api.views.resource_views.check_for_resource_operations')
    def test_admin_cannot_delete_attached_resource(self, 
        mock_check_for_resource_operations,
        mock_api_tasks,
        mock_file_check):
        """
        Test that even admin users cannot delete an attached Resource that has
        been used in a Workspace
        """
        mock_file_check.return_value = False
        mock_check_for_resource_operations.return_value = True
        response = self.authenticated_admin_client.delete(self.url_for_workspace_resource)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_api_tasks.delete_file.delay.assert_not_called()
        r = Resource.objects.get(pk=self.regular_user_workspace_resource.pk)

    def test_users_can_view_own_resource_detail(self):
        """
        Test that regular users can view their own Resource detail.
        """
        response = self.authenticated_regular_client.get(self.url_for_unattached)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(str(self.regular_user_unattached_resource.pk), response.data['id'])

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    def test_users_can_delete_own_unused_resource(self, 
        mock_api_tasks,
        mock_file_check):
        """
        Test that regular users can delete their own Resource IF IT IS
        NOT associated with a Workspace.

        Here, no other Resources reference the file that this particular
        Resource is pointing at.  We can then safely call for deletion on 
        the Resoure AND the file
        """
        mock_file_check.return_value = False
        response = self.authenticated_regular_client.delete(self.url_for_active_unattached)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_api_tasks.delete_file.delay.assert_called()
        with self.assertRaises(Resource.DoesNotExist):
            Resource.objects.get(pk=self.regular_user_active_unattached_resource.pk)

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    def test_users_can_delete_own_unused_resource_case2(self, 
        mock_api_tasks,
        mock_file_check):
        """
        Test that regular users can delete their own Resource IF IT IS
        NOT associated with a Workspace.

        Here, there are somehow other Resources referencing the same 
        underlying file (via the mocked return from check_for_shared_resource_file).  
        
        Then we have to assert that the async delete
        was NOT called, but the Resource database record was.
        """
        mock_file_check.return_value = True
        response = self.authenticated_regular_client.delete(self.url_for_active_unattached)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_api_tasks.delete_file.delay.assert_not_called()
        with self.assertRaises(Resource.DoesNotExist):
            Resource.objects.get(pk=self.regular_user_active_unattached_resource.pk)

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    @mock.patch('api.views.resource_views.check_for_resource_operations')
    def test_user_can_remove_unused_resource_from_workspace(self, 
        mock_check_for_resource_operations,
        mock_api_tasks,
        mock_file_check):
        """
        Test that regular users can delete their own Resource if it has 
        NOT been used within a Workspace.  Here we check that both the Resource
        AND file are deleted.
        """
        mock_file_check.return_value = False
        mock_check_for_resource_operations.return_value = False
        response = self.authenticated_regular_client.delete(self.url_for_workspace_resource)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_api_tasks.delete_file.delay.assert_called()
        with self.assertRaises(Resource.DoesNotExist):
            Resource.objects.get(pk=self.regular_user_workspace_resource.pk)

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    @mock.patch('api.views.resource_views.check_for_resource_operations')
    def test_user_can_remove_unused_resource_from_workspace(self, 
        mock_check_for_resource_operations,
        mock_api_tasks,
        mock_file_check):
        """
        Test that regular users can delete their own Resource if it has 
        NOT been used within a Workspace.

        Here, only the Resource is deleted.
        """
        mock_file_check.return_value = True
        mock_check_for_resource_operations.return_value = False
        response = self.authenticated_regular_client.delete(self.url_for_workspace_resource)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_api_tasks.delete_file.delay.assert_not_called()
        with self.assertRaises(Resource.DoesNotExist):
            Resource.objects.get(pk=self.regular_user_workspace_resource.pk)

    @mock.patch('api.views.resource_views.check_for_shared_resource_file')
    @mock.patch('api.views.resource_views.api_tasks')
    @mock.patch('api.views.resource_views.check_for_resource_operations')
    def test_users_cannot_delete_own_attached_resource(self, 
        mock_check_for_resource_operations,
        mock_api_tasks,
        mock_file_check):
        """
        Users CANNOT remove the resource is it has been
        used by ANY of the operations/analyses associated with the 
        Workspace.

        Test that regular users cannot delete their own Resource if it has been
        used within a Workspace
        """
        mock_file_check.return_value = False
        mock_check_for_resource_operations.return_value = True
        response = self.authenticated_regular_client.delete(self.url_for_workspace_resource)
        mock_api_tasks.delete_file.delay.assert_not_called()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
    

    def test_other_users_cannot_delete_resource(self):
        """
        Test that another regular users can't delete someone else's Workpace.
        """
        response = self.authenticated_other_client.delete(self.url_for_unattached)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_user_cannot_view_resource_detail(self):
        """
        Test that another regular user can't view the Workpace detail.
        """
        response = self.authenticated_other_client.get(self.url_for_unattached)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


    def test_users_cannot_change_owner(self):
        '''
        Regular users cannot change the owner of a Resource.  That
        would amount to assigning a Resource to someone else- do not
        want that.
        '''
        payload = {'owner_email':self.regular_user_2.email}
        response = self.authenticated_regular_client.put(
            self.url_for_unattached, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        payload = {'owner_email':self.regular_user_2.email}
        response = self.authenticated_regular_client.put(
            self.url_for_workspace_resource, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_cannot_directly_reassign_resource_to_different_workspace(self):
        '''
        Even if the Resource has not been used, a user cannot directly
        re-assign a Resource to a different Workspace.  They must first unassign 
        it and then assign it to the desired Workspace.  Through that "approved"
        process, the proper copies are made.  Directly editing the Workspace is
        not allowed since it bypasses the copy.
        '''
        # get the workspace to which the resource is assigned:
        workspace1 = self.regular_user_workspace_resource.workspace

        # get another workspace owned by that user:
        all_user_workspaces = Workspace.objects.filter(
            owner=self.regular_user_workspace_resource.owner
        )
        other_workspaces = [x for x in all_user_workspaces if not x==workspace1]
        if len(other_workspaces) == 0:
            raise ImproperlyConfigured('Need to create another Workspace for'
                ' user {user_email}'.format(
                    user_email=self.regular_user_workspace_resource.owner.email
                )
            )
        other_workspace = other_workspaces[0]
        payload = {'workspace': other_workspace.pk}

        # try for a resource already attached to a workspace
        response = self.authenticated_regular_client.put(
            self.url_for_workspace_resource, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # try for an unattached resource (one that has not been assigned to
        # a workspace):
        response = self.authenticated_regular_client.put(
            self.url_for_unattached, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_cannot_change_active_status(self):
        '''
        The `is_active` boolean cannot be altered by a regular user.
        `is_active` is used to block edits while validation is processing, etc.

        The `is_active` is ignored for requests from regular users
        so there is no 400 returned.  Rather, we check that the flag
        has not changed.
        ''' 
        # check that it was not active to start:
        self.assertTrue(self.regular_user_workspace_resource.is_active)
        payload = {'is_active': False}
        response = self.authenticated_regular_client.put(
            self.url_for_workspace_resource, payload, format='json'
        )
        r = Resource.objects.get(pk=self.regular_user_workspace_resource.pk)
        self.assertTrue(r.is_active)

    def test_admin_cannot_change_active_status(self):
        '''
        The `is_active` boolean cannot be reset via the API, even by
        an admin
        ''' 
        # find the status at the start:
        initial_status = self.regular_user_unattached_resource.is_active
        final_status = not initial_status

        payload = {'is_active': final_status}
        response = self.authenticated_admin_client.put(
            self.url_for_unattached, payload, format='json'
        )
        r = Resource.objects.get(pk=self.regular_user_unattached_resource.pk)

        # check that the bool changed:
        self.assertEqual(r.is_active, initial_status)


    def test_user_cannot_change_status_message(self):
        '''
        The `status` string canNOT be reset by a regular user
        ''' 
        # check that it was not active to start:
        orig_status = self.regular_user_unattached_resource.status

        payload = {'status': 'something'}
        response = self.authenticated_regular_client.put(
            self.url_for_unattached, payload, format='json'
        )
        r = Resource.objects.get(pk=self.regular_user_unattached_resource.pk)
        self.assertTrue(r.status == orig_status)

    def test_admin_can_change_status_message(self):
        '''
        The `status` string can be reset by an admin
        ''' 
        # check that it was not active to start:
        orig_status = self.active_resource.status

        payload = {'status': 'something'}
        response = self.authenticated_admin_client.put(
            self.url_for_active_resource, payload, format='json'
        )
        r = Resource.objects.get(pk=self.active_resource.pk)
        self.assertFalse(r.status == orig_status)

    def test_user_cannot_change_date_added(self):
        '''
        Once the Resource has been added, there is no editing
        of the DateTime.
        '''
        orig_datetime = self.regular_user_unattached_resource.creation_datetime
        original_pk = self.regular_user_unattached_resource.pk

        date_str = 'May 20, 2018 (16:00:07)'
        payload = {'created': date_str}
        response = self.authenticated_regular_client.put(
            self.url_for_unattached, payload, format='json'
        )
        # since the field is ignored, it will not raise any exception.
        # Still want to check that the object is unchanged:
        r = Resource.objects.get(pk=original_pk)
        self.assertEqual(orig_datetime, r.creation_datetime)
        orig_datestring = orig_datetime.strftime('%B %d, %Y, (%H:%M:%S)')
        self.assertTrue(orig_datestring != date_str)


    def test_user_can_make_resource_public(self):
        '''
        Make a Resource public so that others may
        see/use it.  Note that use by others creates a copy
        so that the original data remains the same.
        '''
        private_resources = Resource.objects.filter(
            owner = self.regular_user_1,
            is_active = True,
            is_public = False
        )
        if len(private_resources) > 0:
            private_resource = private_resources[0]

            url = reverse(
                'resource-detail', 
                kwargs={'pk':private_resource.pk}
            )
            payload = {'is_public': True}
            response = self.authenticated_regular_client.put(
                url, payload, format='json'
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            r = Resource.objects.get(pk=private_resource.pk)
            self.assertTrue(r.is_public)

        else:
            raise ImproperlyConfigured('To properly run this test, you'
            ' need to have at least one public Resource.')

    def test_user_can_make_resource_private(self):
        '''
        If a Resource was public, make it private.
        This will NOT "recall" datasets that were derived
        from this (e.g. if someone else used it while it was public)
        '''
        active_and_public_resources = Resource.objects.filter(
            is_active = True,
            is_public = True,
            owner = self.regular_user_1
        )
        if len(active_and_public_resources) == 0:
            raise ImproperlyConfigured('To properly run this test, you'
            ' need to have at least one public AND active Resource.')
        r = active_and_public_resources[0]
        url = reverse(
            'resource-detail', 
            kwargs={'pk':r.pk}
        )
        payload = {'is_public': False}
        response = self.authenticated_regular_client.put(
            url, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        updated_resource = Resource.objects.get(pk=r.pk)
        self.assertFalse(updated_resource.is_public)



    def test_cannot_make_changes_when_inactive(self):
        '''
        Test that no changes can be made when the resource is inactive.
        '''
        self.assertFalse(self.inactive_resource.is_active)

        # just try to change the path as an example
        payload = {'path': '/some/path/to/file.txt'}
        response = self.authenticated_admin_client.put(
            self.url_for_inactive_resource, payload, format='json'
        )
        self.assertTrue(response.status_code == status.HTTP_400_BAD_REQUEST)

    def test_admin_can_change_path(self):
        '''
        Path is only relevant for internal/database use so 
        users cannot change that. Admins may, however
        '''
        self.assertTrue(self.active_resource.is_active)
        original_path = self.active_resource.path
        new_path = '/some/new/path.txt'
        payload = {'path': new_path}
        response = self.authenticated_admin_client.put(
            self.url_for_active_resource, payload, format='json'
        )
        # query db for that same Resource object and verify that the path
        # has not been changed:
        obj = Resource.objects.get(pk=self.active_resource.pk)
        self.assertEqual(obj.path, new_path)
        self.assertFalse(obj.path == original_path)

    def test_user_cannot_change_path(self):
        '''
        Path is only relevant for internal/database use so 
        users cannot change that.
        '''
        original_path = self.regular_user_unattached_resource.path
        payload = {'path': '/some/new/path.txt'}
        response = self.authenticated_regular_client.put(
            self.url_for_unattached, payload, format='json'
        )
        # query db for that same Resource object and verify that the path
        # has not been changed:
        obj = Resource.objects.get(pk=self.regular_user_unattached_resource.pk)
        self.assertEqual(obj.path, original_path)


    def test_user_can_change_resource_name(self):
        '''
        Users may change the Resource name.  This does NOT
        change anything about the path, etc.
        '''
        original_name = self.active_resource.name

        payload = {'name': 'newname.txt'}
        response = self.authenticated_regular_client.put(
            self.url_for_active_resource, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        json_obj = response.json()
        self.assertTrue(json_obj['name'], 'newname.txt')

        # just double check that the original name wasn't the same
        # by chance
        self.assertTrue(original_name != 'newname.txt')

    @mock.patch('api.serializers.resource.api_tasks')
    def test_changing_resource_type_resets_status(self,  
        mock_api_tasks):
        '''
        If an attempt is made to change the resource type
        ensure that the resource has its "active" state 
        set to False and that the status changes.

        Since the validation can take some time, it will call
        the asynchronous validation process.
        '''
        current_resource_type = self.active_resource.resource_type
        other_types = set(
            [x[0] for x in DATABASE_RESOURCE_TYPES]
            ).difference(set([current_resource_type]))
        newtype = list(other_types)[0]

        # verify that we are actually changing the type 
        # in this request (i.e. not a trivial test)
        self.assertFalse(
            newtype == current_resource_type
        )
        payload = {'resource_type': newtype}
        response = self.authenticated_regular_client.put(
            self.url_for_active_resource, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        r = Resource.objects.get(pk=self.active_resource.pk)

        # active state set to False
        self.assertFalse(r.is_active)

        # check that the status message changed.  technically possible
        # that the original and "updated" status messages are the same
        # but it would be obvious if that happens since the test would fail.
        self.assertTrue(r.status == Resource.VALIDATING)

        # check that the validation method was called.
        mock_api_tasks.validate_resource.delay.assert_called_with(self.active_resource.pk, newtype)


    def test_setting_workspace_to_null_fails(self):
        '''
        Test that directly setting the workspace to null fails.
        Users can't change a Resource's workspace.  They can only 
        remove unused Resources from a Workspace.
        '''
        payload = {'workspace': None}

        # try for an attached resource
        response = self.authenticated_regular_client.put(
            self.url_for_workspace_resource, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # try for an unattached resource
        response = self.authenticated_regular_client.put(
            self.url_for_unattached, payload, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class ResourcePreviewTests(BaseAPITestCase):

    def setUp(self):

        self.establish_clients()

        # get an example from the database:
        regular_user_resources = Resource.objects.filter(
            owner=self.regular_user_1,
        )
        if len(regular_user_resources) == 0:
            msg = '''
                Testing not setup correctly.  Please ensure that there is at least one
                Resource instance for the user {user}
            '''.format(user=self.regular_user_1)
            raise ImproperlyConfigured(msg)

        self.resource = regular_user_resources[0]
        self.url = reverse(
            'resource-preview', 
            kwargs={'pk':self.resource.pk}
        )

        for r in regular_user_resources:
            if not r.is_active:
                inactive_resource = r
                break
        self.inactive_resource_url = reverse(
            'resource-preview', 
            kwargs={'pk':inactive_resource.pk}
        )

    def test_preview_request_from_non_owner(self):
        '''
        Tests where a preview is requested from someone else's
        resource
        '''
        response = self.authenticated_other_client.get(
            self.url, format='json'
        )
        self.assertEqual(response.status_code, 
            status.HTTP_403_FORBIDDEN)

    def test_preview_request_for_inactive_fails(self):
        '''
        Tests where a preview is requested for a resource
        that is inactive.
        '''
        response = self.authenticated_regular_client.get(
            self.inactive_resource_url, format='json'
        )
        self.assertEqual(response.status_code, 
            status.HTTP_400_BAD_REQUEST)


    @mock.patch('api.views.resource_views.get_resource_preview')
    def test_error_reported(self, mock_preview):
        '''
        If there was some error in preparing the preview, 
        the returned data will have an 'error' key
        '''
        mock_preview.return_value = {'error': 'something'}
        response = self.authenticated_regular_client.get(
            self.url, format='json'
        )
        self.assertEqual(response.status_code, 
            status.HTTP_500_INTERNAL_SERVER_ERROR)   

        self.assertTrue('error' in response.json())     

    @mock.patch('api.views.resource_views.get_resource_preview')
    def test_expected_response(self, mock_preview):
        '''
        If there was some error in preparing the preview, 
        the returned data will have an 'error' key
        '''
        preview_dict = {
            'columns': ['a', 'b', 'c'],
            'rows': [1,2,3], 
            'values': [[0,1,2],[3,4,5],[6,7,8]]}
        mock_preview.return_value = preview_dict
        response = self.authenticated_regular_client.get(
            self.url, format='json'
        )
        self.assertEqual(response.status_code, 
            status.HTTP_200_OK)
        j = json.loads(response.json())
        self.assertDictEqual(preview_dict, j) 