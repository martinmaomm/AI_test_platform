from django.contrib.auth import get_user_model
from django.test import TestCase

from api_testing.models import APITestSuite
from projects.models import Environment, Project
from web_testing.models import WebUITestSuite

from .serializers import ScheduledTaskCreateSerializer


class ScheduledTaskEnvironmentContractTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='scheduled-task-contract', password='test-password',
        )
        self.project = Project.objects.create(
            name='Scheduled task contract', project_type='web',
            owner=self.user, created_by=self.user,
        )
        self.environment = Environment.objects.create(
            project=self.project, name='API environment',
            category=Environment.EnvironmentCategory.API,
            config={'base_url': 'https://api.example.test'}, is_active=True,
        )
        self.web_suite = WebUITestSuite.objects.create(
            name='Web suite', user=self.user, project=self.project,
        )
        self.api_suite = APITestSuite.objects.create(
            name='API suite', user=self.user, project=self.project,
        )

    def payload(self, suite_type, suite_id, **overrides):
        payload = {
            'name': f'{suite_type} schedule',
            'suite_type': suite_type,
            'suite_ids': [suite_id],
            'cron_expression': '0 9 * * 1-5',
        }
        payload.update(overrides)
        return payload

    def test_webui_schedule_allows_no_environment_and_persists_null(self):
        serializer = ScheduledTaskCreateSerializer(data=self.payload('web', self.web_suite.id))

        self.assertTrue(serializer.is_valid(), serializer.errors)
        task = serializer.save(user=self.user, project=self.project)
        self.assertIsNone(task.environment)

    def test_webui_schedule_rejects_environment(self):
        serializer = ScheduledTaskCreateSerializer(data=self.payload(
            'web', self.web_suite.id, environment=self.environment.id,
        ))

        self.assertFalse(serializer.is_valid())
        self.assertIn('environment', serializer.errors)

    def test_api_schedule_requires_environment_on_create_and_update(self):
        missing_environment = ScheduledTaskCreateSerializer(data=self.payload('api', self.api_suite.id))
        self.assertFalse(missing_environment.is_valid())
        self.assertIn('environment', missing_environment.errors)

        create_serializer = ScheduledTaskCreateSerializer(data=self.payload(
            'api', self.api_suite.id, environment=self.environment.id,
        ))
        self.assertTrue(create_serializer.is_valid(), create_serializer.errors)
        task = create_serializer.save(user=self.user, project=self.project)

        update_serializer = ScheduledTaskCreateSerializer(
            task, data={'environment': None}, partial=True,
        )
        self.assertFalse(update_serializer.is_valid())
        self.assertIn('environment', update_serializer.errors)

    def test_switching_api_schedule_to_webui_clears_former_environment(self):
        create_serializer = ScheduledTaskCreateSerializer(data=self.payload(
            'api', self.api_suite.id, environment=self.environment.id,
        ))
        self.assertTrue(create_serializer.is_valid(), create_serializer.errors)
        task = create_serializer.save(user=self.user, project=self.project)

        update_serializer = ScheduledTaskCreateSerializer(
            task,
            data={'suite_type': 'web', 'suite_ids': [self.web_suite.id]},
            partial=True,
        )
        self.assertTrue(update_serializer.is_valid(), update_serializer.errors)
        updated = update_serializer.save()
        self.assertIsNone(updated.environment)
