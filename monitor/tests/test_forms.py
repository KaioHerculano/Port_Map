from django.test import TestCase
from faker import Faker

from monitor.forms import DeviceForm

fake = Faker()


class DeviceFormTests(TestCase):
    def test_device_form_valid_data_defaults(self):
        form_data = {
            "name": fake.company(),
            "host": fake.ipv4(),
            "device_type": "mikrotik_snmp",
            "check_interval": 5,
            "telegram_alert_threshold": 2,
        }
        form = DeviceForm(data=form_data)
        self.assertTrue(form.is_valid())

        # Check default values supplied by clean()
        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data["snmp_community"], "public")
        self.assertEqual(cleaned_data["snmp_port"], 161)
        self.assertEqual(cleaned_data["api_port"], 8728)

    def test_device_form_non_required_fields(self):
        form = DeviceForm()
        self.assertFalse(form.fields["api_username"].required)
        self.assertFalse(form.fields["api_password"].required)
        self.assertFalse(form.fields["api_port"].required)
        self.assertFalse(form.fields["snmp_community"].required)
        self.assertFalse(form.fields["snmp_port"].required)

    def test_device_form_invalid_data(self):
        form_data = {
            "name": "",  # Name is required
            "host": fake.ipv4(),
            "device_type": "mikrotik_snmp",
            "check_interval": 5,
            "telegram_alert_threshold": 2,
        }
        form = DeviceForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
