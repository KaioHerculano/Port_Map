from django.test import TestCase
from django.contrib.auth import get_user_model, authenticate

class AuthenticationBackendTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="john_doe",
            email="john@example.com",
            password="mypassword456"
        )

    def test_authenticate_by_username(self):
        user = authenticate(username="john_doe", password="mypassword456")
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user)

    def test_authenticate_by_email(self):
        user = authenticate(username="john@example.com", password="mypassword456")
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user)

    def test_authenticate_by_email_case_insensitive(self):
        user = authenticate(username="JOHN@example.com", password="mypassword456")
        self.assertIsNotNone(user)
        self.assertEqual(user, self.user)

    def test_authenticate_wrong_password_fails(self):
        user = authenticate(username="john_doe", password="wrongpassword")
        self.assertIsNone(user)
        
    def test_authenticate_nonexistent_user_fails(self):
        user = authenticate(username="ghost_user", password="somepassword")
        self.assertIsNone(user)
