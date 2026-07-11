from ..state import get_active_identity_id, has_active_identity_context, has_identity


class IdentityContextRequired(RuntimeError):
    pass


def require_identity_id(send_as_id=None):
    if send_as_id is not None:
        try:
            identity_id = int(send_as_id)
        except (TypeError, ValueError) as exc:
            raise IdentityContextRequired(f"invalid identity: {send_as_id!r}") from exc
        if identity_id > 0 and has_identity(identity_id):
            return identity_id
        raise IdentityContextRequired(f"unknown identity: {identity_id}")

    if has_active_identity_context():
        identity_id = int(get_active_identity_id() or 0)
        if identity_id > 0 and has_identity(identity_id):
            return identity_id
    raise IdentityContextRequired("no active identity context")


__all__ = ["IdentityContextRequired", "require_identity_id"]
