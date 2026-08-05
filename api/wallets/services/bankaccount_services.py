import logging

from django.utils import timezone

from api.users.enums import UserRole
from api.wallets.models.bankaccount_models import BankAccount
from shared.audit.audit_service import AuditService
from shared.services.paystack_service import PaystackService
from shared.utils.encryption import encrypt_field

logger = logging.getLogger(__name__)


class AccountDetailsError(Exception):
    """Custom exception for account details errors."""
    
    

def _check_account_name_matches_profile(user, account_name):
        full_name = user.full_name.lower()
        user_names = set(full_name.split())
        account_names = set(account_name.lower().split())

        common_names = user_names.intersection(account_names)
        return len(common_names) >= 2


def get_bank_account_list(user):
    """
    Get the list of bank accounts for a user.
    """
    qs = BankAccount.objects.filter(is_deleted=False)

    if user.role not in [UserRole.ADMIN, UserRole.SUPER_ADMIN]:
        qs = qs.filter(user=user)

    return qs


def create_bank_account(user, validated_data, ip, ua):
    """
        Create a new bank account for a user.
    """

    account_name = validated_data.get("account_name", "")
    account_number = validated_data.get("account_number", "")
    bank_code = validated_data.get("bank_code", "")
    
    if not _check_account_name_matches_profile(user, account_name):
        raise AccountDetailsError("Account name does not match user profile.")

    existing_accounts = BankAccount.objects.filter(user=user)
    if bank_code:
        existing_accounts = existing_accounts.filter(bank_code=bank_code)
    else:
        existing_accounts = existing_accounts.filter(bank_name=validated_data.get("bank_name", ""))
    
    existing_account = existing_accounts.filter(account_number=encrypt_field(account_number)).first()
    if existing_account:
        if existing_account.is_suspended:
            raise AccountDetailsError("This bank account is suspended.")
        else:
            existing_account.is_default = validated_data.get("is_default", existing_account.is_default)
            existing_account.is_deleted = False
            existing_account.deleted_datetime = None
            existing_account.save()
        existing_account.refresh_from_db()
        return existing_account

    validated_data["account_number"] = encrypt_field(account_number)
    validated_data["bank_name"] = PaystackService.get_bank_name(bank_code)
    account =  BankAccount.objects.create(
        user=user,
        **validated_data,
    )

    try:
        AuditService.log_event(
            "BANK_ACCOUNT_ADDED", 
            email=user.email, 
            ip=ip, 
            user_agent=ua, 
            metadata={'account_id': account.id}
        )
    except Exception as e:
        # Log the error but do not raise it, as the account creation has already succeeded
        logger.warning(f"Error while logging audit event: {e!s}")

    return account


def delete_bank_account(user, account_id, ip, ua):
    """
        Delete a bank account for a user.
    """
    try:
        account = BankAccount.objects.get(id=account_id, user=user, is_deleted=False)
    except BankAccount.DoesNotExist:
        raise AccountDetailsError("Bank account not found.")

    account.is_deleted = True
    account.is_default = False  # Reset default status if the account is deleted
    account.deleted_datetime = timezone.now()
    account.save()

    try:
        AuditService.log_event(
            "BANK_ACCOUNT_DELETED", 
            email=user.email, 
            ip=ip, 
            user_agent=ua, 
            metadata={'account_id': account.id}
            )
    except Exception as e:
        # Log the error but do not raise it, as the account deletion has already succeeded
        logger.warning(f"Error while logging audit event: {e!s}")


def set_default_bank_account(user, account_id, ip, ua):
    """
        Set a bank account as the default for a user.
    """
    account = BankAccount.objects.get(id=account_id, user=user, is_deleted=False)

    # Set the selected account as default
    account.is_default = True
    account.save()
    try:
        AuditService.log_event(
            "BANK_ACCOUNT_SET_DEFAULT", 
            email=user.email, 
            ip=ip, 
            user_agent=ua, 
            metadata={'account_id': account.id}
        )
    except Exception as e:
        # Log the error but do not raise it, as the account update has already succeeded
        logger.warning(f"Error while logging audit event: {e!s}")

def suspend_bank_account(user, account_id, ip, ua):
    """
        Suspend a bank account for a user.
    """
    account = BankAccount.objects.get(id=account_id, is_deleted=False)

    # Suspend the selected account
    account.is_suspended = True
    account.save()
    try:
        AuditService.log_event(
            "BANK_ACCOUNT_SUSPENDED", 
            email=user.email, 
            ip=ip, 
            user_agent=ua, 
            metadata={'account_id': account.id}
        )
    except Exception as e:
        # Log the error but do not raise it, as the account suspension has already succeeded
        logger.warning(f"Error while logging audit event: {e!s}")
