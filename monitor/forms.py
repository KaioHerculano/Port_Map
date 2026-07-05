from django import forms

from .models import Device

_DEVICE_INPUT_CLASS = (
    "w-full py-2.5 px-4 rounded-lg border border-zinc-800 bg-zinc-950/50 text-sm "
    "text-base-content placeholder:text-zinc-500 focus:outline-none focus:ring-2 "
    "focus:ring-violet-500/50 focus:border-violet-500/50 transition-colors"
)


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = [
            "name",
            "host",
            "device_type",
            "group",
            "is_active",
            "check_interval",
            "telegram_alert_threshold",
            "snmp_community",
            "snmp_port",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": _DEVICE_INPUT_CLASS,
                    "placeholder": "Ex: OLT Parks GPON, MikroTik BGP",
                }
            ),
            "host": forms.TextInput(
                attrs={
                    "class": _DEVICE_INPUT_CLASS,
                    "placeholder": "Ex: 172.31.255.2",
                }
            ),
            "device_type": forms.Select(attrs={"class": _DEVICE_INPUT_CLASS}),
            "group": forms.Select(attrs={"class": _DEVICE_INPUT_CLASS}),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "checkbox checkbox-sm checkbox-primary",
                }
            ),
            "check_interval": forms.Select(attrs={"class": _DEVICE_INPUT_CLASS}),
            "telegram_alert_threshold": forms.Select(
                attrs={"class": _DEVICE_INPUT_CLASS}
            ),
            "snmp_community": forms.TextInput(
                attrs={
                    "class": _DEVICE_INPUT_CLASS,
                    "placeholder": "public",
                }
            ),
            "snmp_port": forms.NumberInput(attrs={"class": _DEVICE_INPUT_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["snmp_community"].required = False
        self.fields["snmp_port"].required = False

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("snmp_community"):
            cleaned_data["snmp_community"] = "public"
        if not cleaned_data.get("snmp_port"):
            cleaned_data["snmp_port"] = 161
        return cleaned_data
