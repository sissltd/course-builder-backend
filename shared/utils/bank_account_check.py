def check_account_name_matches_profile(names: set, account_name: str) -> bool:
    """Takes the set of first and last names as 'names', and checks if it matches the account name provided.

    Returns True if at least two names match, False otherwise.
    """
    
    user_names = {name.lower() for name in names}
    account_names = set(account_name.lower().split())

    common_names = user_names.intersection(account_names)
    return len(common_names) >= 2