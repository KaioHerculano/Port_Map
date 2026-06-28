from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

class AccountsModelTests(TestCase):
    def test_create_user_successful(self):
        User = get_user_model()
        user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="securepassword123"
        )
        self.assertEqual(user.username, "testuser")
        self.assertEqual(user.email, "test@example.com")
        self.assertTrue(user.check_password("securepassword123"))

    def test_duplicate_email_raises_error(self):
        User = get_user_model()
        User.objects.create_user(
            username="user1",
            email="duplicate@example.com",
            password="password1"
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username="user2",
                email="duplicate@example.com",
                password="password2"
            )
