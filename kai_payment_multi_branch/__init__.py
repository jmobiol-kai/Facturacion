from . import models
from . import wizard


def uninstall_hook(env):
    """Restore the journal-item company rule. It must not mention our field after uninstall."""
    rule = env.ref("account.account_move_line_comp_rule", raise_if_not_found=False)
    if rule:
        rule.domain_force = "[('company_id', 'in', company_ids)]"
