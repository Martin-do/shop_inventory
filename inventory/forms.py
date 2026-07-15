from decimal import Decimal

from django import forms
from django.contrib.auth.models import User

from .models import Category, Product, Customer, StoreSettings


class ProductForm(forms.ModelForm):
    opening_stock = forms.IntegerField(min_value=0, initial=0, required=False)
    new_category = forms.CharField(
        max_length=120,
        required=False,
        help_text="Type a name here to create a new category (used if set).",
        widget=forms.TextInput(attrs={"placeholder": "Or type a new category", "autocomplete": "off"}),
    )

    class Meta:
        model = Product
        fields = ["name", "variant", "barcode", "category", "cost_price", "selling_price", "reorder_level", "image", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"autofocus": True}),
            "barcode": forms.TextInput(attrs={"autocomplete": "off"}),
            "variant": forms.TextInput(attrs={"placeholder": "e.g. 1L, 1.5L, Pack of 6"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["category"].empty_label = "— Select a category —"
        self.fields["cost_price"].required = False
        self.fields["cost_price"].label = "Cost Price (₦)"
        self.fields["selling_price"].label = "Selling Price (₦)"
        if self.instance.pk:
            # Editing an existing product: opening stock only applies on create.
            self.fields.pop("opening_stock")
        else:
            # Creating: new products are active by default; no toggle needed yet.
            self.fields.pop("is_active")
        self.order_fields(["name", "variant", "barcode", "category", "new_category", "cost_price", "selling_price", "reorder_level", "image", "opening_stock"])

    def clean_new_category(self):
        return self.cleaned_data.get("new_category", "").strip()

    def save(self, commit=True):
        new_category = self.cleaned_data.get("new_category")
        if new_category:
            category, _ = Category.objects.get_or_create(name=new_category)
            self.instance.category = category
        return super().save(commit=commit)

    def clean_barcode(self):
        return self.cleaned_data["barcode"].strip()

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_cost_price(self):
        return self.cleaned_data.get("cost_price") or Decimal("0.00")


class ReceiveStockForm(forms.Form):
    barcode = forms.CharField(
        max_length=80,
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "off", "inputmode": "numeric"}),
    )
    quantity = forms.IntegerField(min_value=1, widget=forms.NumberInput(attrs={"min": 1}))
    note = forms.CharField(
        max_length=240,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "Supplier, invoice, or reason"}),
    )

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip()
        if not Product.objects.filter(barcode=barcode, is_active=True).exists():
            raise forms.ValidationError("No active product was found for this barcode.")
        return barcode

    def clean_note(self):
        return self.cleaned_data.get("note", "").strip()


class AddToCartForm(forms.Form):
    barcode = forms.CharField(max_length=80)
    quantity = forms.IntegerField(min_value=1, initial=1)

    def clean_barcode(self):
        barcode = self.cleaned_data["barcode"].strip()
        if not Product.objects.filter(barcode=barcode, is_active=True).exists():
            raise forms.ValidationError("No active product was found for this barcode.")
        return barcode


class CheckoutForm(forms.Form):
    cashier_name = forms.CharField(max_length=120, required=False)
    customer = forms.ModelChoiceField(queryset=Customer.objects.all(), required=False)
    discount_amount = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=12, required=False, initial=Decimal("0.00"))
    tax_rate = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=5, required=False, initial=Decimal("0.00"))
    amount_paid = forms.DecimalField(min_value=Decimal("0.00"), decimal_places=2, max_digits=12)

    def clean_cashier_name(self):
        return self.cleaned_data.get("cashier_name", "").strip()


class StoreSettingsForm(forms.ModelForm):
    class Meta:
        model = StoreSettings
        fields = [
            "store_name",
            "store_logo",
            "default_tax_rate",
            "enable_tax",
            "receipt_header",
            "receipt_footer",
            "google_drive_folder_id",
            "google_service_account_json",
            "auto_backup_enabled",
            "auto_backup_interval_hours"
        ]
        widgets = {
            "receipt_header": forms.Textarea(attrs={"rows": 3}),
            "receipt_footer": forms.Textarea(attrs={"rows": 3}),
            "google_service_account_json": forms.Textarea(attrs={"rows": 6, "placeholder": '{"type": "service_account", ...}'}),
        }

    def clean_google_service_account_json(self):
        val = self.cleaned_data.get("google_service_account_json", "")
        if val:
            val = val.strip()
            if not val:
                return ""
            try:
                import json
                json.loads(val)
            except ValueError:
                raise forms.ValidationError("Must be a valid Service Account Credentials JSON.")
        return val


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "notes"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def clean_phone(self):
        return self.cleaned_data.get("phone", "").strip()

    def clean_email(self):
        return self.cleaned_data.get("email", "").strip()


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class StaffForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(), required=False, help_text="Leave blank to keep current password.")

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["password"].required = True
            self.fields["password"].help_text = "Required for new staff."

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        if commit:
            user.save()
        return user
