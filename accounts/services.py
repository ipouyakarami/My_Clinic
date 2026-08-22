from django.contrib.auth import get_user_model

from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress, EmailConfirmation

User = get_user_model()


def send_activation_email(request, user):
    email_address, _ = EmailAddress.objects.get_or_create(
        user=user,
        defaults={"email": user.email, "verified": False, "primary": True},
    )
    confirmation = EmailConfirmation.create(email_address)
    try:
        confirmation.send(request, signup=True)
    except Exception:
        return False
    return True


def activate_account(confirmation, password):
    user = confirmation.email_address.user
    user.set_password(password)
    user.is_active = True
    user.save()
    EmailConfirmation.objects.filter(email_address=confirmation.email_address).delete()
    return user


def create_patient_with_profile(form_data):
    from .models import Patient

    user = User.objects.create_user(
        email=form_data["email"],
        password=None,
        first_name=form_data["first_name"],
        last_name=form_data["last_name"],
        user_type=User.UserType.PATIENT,
        is_active=False,
    )
    Patient.objects.create(user=user, phone_number=form_data.get("phone_number", ""))
    return user
