from django.apps import AppConfig
from django.db.models.signals import post_save


class CollaboratorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api.collaborators"

    def ready(self):
        from api.collaborators.models import WorkspaceCollaborator
        from api.users.models import User

        def link_pending_workspace_invites_to_user(sender, instance, created, **kwargs):
            """When a User is created, accept any pending workspace invites
            matching their email.

            Signing up with the invited email means they accepted, so this
            connects the two immediately - via the same
            collaborator_service.accept_workspace_invite step the explicit
            accept endpoint uses - so the collaborator appears in the
            creator's Collaborators list.
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

            from api.collaborators.services import collaborator_service

            for invite in pending_invites:
                collaborator_service.accept_workspace_invite(
                    invite=invite, user=instance
                )

        post_save.connect(link_pending_workspace_invites_to_user, sender=User)
