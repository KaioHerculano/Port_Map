from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase
from faker import Faker

from accounts.models import CustomUser


class AccountsModelTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.faker = Faker()

        cls.user_email = cls.faker.email()
        cls.base_user = get_user_model().objects.create_user(
            username=cls.faker.user_name(),
            email=cls.user_email,
            password=cls.faker.password(),
        )

    def test_create_user_successful(self):
        self.assertIsNotNone(self.base_user.id)
        self.assertEqual(str(self.base_user), self.base_user.username)

    def test_duplicate_email_raises_error(self):
        User = get_user_model()

        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username=self.faker.user_name(),
                email=self.user_email,
                password=self.faker.password(),
            )


class CustomUserTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.faker = Faker()

        cls.base_user = CustomUser.objects.create_user(
            username=cls.faker.user_name(),
            email=cls.faker.email(),
            password=cls.faker.password(),
        )

    def test_custom_user_str_return_username(self):
        self.assertIsNotNone(self.base_user.id)

        self.assertEqual(str(self.base_user), self.base_user.username)
