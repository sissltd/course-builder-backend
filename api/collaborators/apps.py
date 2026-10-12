from django.apps import AppConfig
from django.db.models.signals import post_save


class CollaboratorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api.collaborators"

    def ready(self):
        from api.collaborators.models import WorkspaceCollaborator
        from api.users.models import User

        def link_pending_workspace_invites_to_user(sender, instance, created, **kwargs):
            """When a User is created, link them to any pending workspace invites
            matching their email.

            This is the missing acceptance step for workspace-level collaboration
            invites: the creator sends an invite by email, the invitee signs up with
            that email, and this signal automatically connects the two so the
            collaborator appears in the creator's Collaborators list.
            """
            if not isinstance(instance, User) or not created:
                return

            pending_invites = WorkspaceCollaborator.objects.filter(
                invited_email__iexact=instance.email,
                status=WorkspaceCollaborator.Status.PENDING,
                user__isnull=True,
            )
            if not pending_invites.exists():
                return

            for invite in pending_invites:
                invite.user = instance
                invite.status = WorkspaceCollaborator.Status.ACTIVE
                invite.save(update_fields=["user_id", "status", "updated_datetime"])

        post_save.connect(link_pending_workspace_invites_to_user, sender=User)
