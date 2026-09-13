import random
import string

from dev_helper.common.clipboard import copy_to_clipboard

def _generate_password():
    length = random.randint(30, 60)
    chars = string.ascii_letters + string.digits + "_="
    return ''.join(random.choice(chars) for _ in range(length))

def main():
    password = _generate_password()
    copy_to_clipboard(password)
    print(password)

if __name__ == "__main__":
    main()