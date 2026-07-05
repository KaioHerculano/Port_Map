from django.test import Client, TestCase
from faker import Faker

fake = Faker()


class SecurityMiddlewareTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_disallowed_host_is_blocked(self):
        random_malicious_host = fake.domain_name()
        response = self.client.get("/", HTTP_HOST=random_malicious_host)
        self.assertEqual(response.status_code, 400)
