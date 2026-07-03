from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from faker import Faker

from accounts.services import UserService


class UserServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.faker = Faker()

        cls.login_username = cls.faker.user_name()
        cls.login_password = cls.faker.password()

        cls.user_password = cls.faker.password()
        cls.base_user = get_user_model().objects.create_user(
            username=cls.faker.user_name(),
            email=cls.faker.email(),
            password=cls.user_password,
        )

    def test_autheticate_and_login_falied(self):
        request = RequestFactory().post("/login/")
        with self.assertLogs("accounts.services", level="WARNING") as log_captured:
            user = UserService.authenticate_and_login(
                request, username=self.login_username, password=self.login_password
            )
            self.assertIsNone(user)
            self.assertIn(
                f"Tentativa de login malsucedida para o usuario: {self.login_username}",
                log_captured.output[0],
            )

    def test_sucess_logout_authenticated(self):
        self.client.login(username=self.base_user.username, password=self.user_password)

        request = RequestFactory().post("/logout/")
        request.user = self.base_user

        request.session = self.client.session

        with self.assertLogs("accounts.services", level="INFO") as log_captured:
            returned_username = UserService.logout_user(request)

        self.assertEqual(returned_username, self.base_user.username)
        self.assertIn(
            f"Usuario {self.base_user.username} fez logout.", log_captured.output[0]
        )

    def test_sucess_logout_anonymous(self):
        request = RequestFactory().post("/logout/")
        request.user = AnonymousUser()
        request.session = self.client.session

        with self.assertLogs("accounts.services", level="INFO") as log_captured:
            returned_username = UserService.logout_user(request)

        self.assertEqual(returned_username, "anonymous")
        self.assertIn("Usuario anonymous fez logout.", log_captured.output[0])
