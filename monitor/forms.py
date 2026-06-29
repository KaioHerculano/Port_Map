from django import forms
from .models import Device, Group

class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = [
            'name', 'host', 'device_type', 'group', 'is_active', 
            'snmp_community', 'snmp_port', 
            'api_username', 'api_password', 'api_port'
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input', 
                'placeholder': 'Ex: OLT Parks GPON, MikroTik BGP',
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'host': forms.TextInput(attrs={
                'class': 'form-input', 
                'placeholder': 'Ex: 172.31.255.2',
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'device_type': forms.Select(attrs={
                'class': 'form-input',
                'style': 'width: 100%; padding: 0.75rem; background: #1a1a24; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'group': forms.Select(attrs={
                'class': 'form-input',
                'style': 'width: 100%; padding: 0.75rem; background: #1a1a24; border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'style': 'width: 18px; height: 18px; accent-color: var(--primary); cursor: pointer;'
            }),
            'snmp_community': forms.TextInput(attrs={
                'class': 'form-input', 
                'placeholder': 'public',
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'snmp_port': forms.NumberInput(attrs={
                'class': 'form-input', 
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'api_username': forms.TextInput(attrs={
                'class': 'form-input', 
                'placeholder': 'Ex: admin',
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'api_password': forms.PasswordInput(attrs={
                'class': 'form-input', 
                'render_value': True,
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
            'api_port': forms.NumberInput(attrs={
                'class': 'form-input', 
                'style': 'width: 100%; padding: 0.75rem; background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 8px; color: white; font-size: 0.95rem;'
            }),
        }
