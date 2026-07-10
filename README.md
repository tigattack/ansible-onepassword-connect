# Ansible Collection: tigattack.onepassword_connect

[![Documentation][docs_badge]][docs_link]
[![Ansible Galaxy version][galaxy_ver_badge]][galaxy_link]
[![Ansible Galaxy downloads][galaxy_dls_badge]][galaxy_link]

An Ansible collection providing a lookup plugin to retrieve secrets from a
self-hosted [1Password Connect Server](https://developer.1password.com/docs/connect/).

## Requirements

- Ansible-core >= 2.16
- Python >= 3.10 on the controller
- [`onepasswordconnectsdk`](https://pypi.org/project/onepasswordconnectsdk/) >= 2.0.0 on the controller:

  ```bash
  pip install "onepasswordconnectsdk>=2.0.0"
  ```

## Installation

```bash
ansible-galaxy collection install tigattack.onepassword_connect
```

Or, from a local build:

```bash
ansible-galaxy collection build .
ansible-galaxy collection install tigattack-onepassword_connect-*.tar.gz
```

## Configuration

The plugin needs a Connect Server host and API token, supplied either as
lookup parameters or via environment variables:

| Parameter | Environment variable |
|-----------|-----------------------|
| `host`    | `OP_CONNECT_HOST`     |
| `token`   | `OP_CONNECT_TOKEN`    |

## Usage

```yaml
# op:// reference
- name: Fetch a database password
  ansible.builtin.debug:
    msg: "{{ lookup('tigattack.onepassword_connect.item', 'op://Infrastructure/db-primary/password') }}"

# Named parameters
- name: Fetch by vault / item / field names
  ansible.builtin.debug:
    msg: >-
      {{ lookup('tigattack.onepassword_connect.item',
                vault='Infrastructure',
                item='db-primary',
                field='password') }}

# All fields as a dict
- name: Fetch all fields via op:// wildcard
  ansible.builtin.set_fact:
    db_creds: "{{ lookup('tigattack.onepassword_connect.item', 'op://Infra/db-primary/*') }}"
```

See `ansible-doc tigattack.onepassword_connect.item` for the full set of
options and examples, including positional-triple syntax, section-scoped
fields, and per-call host/token overrides.

## See Also

- [`community.general.onepassword`](https://docs.ansible.com/projects/ansible/latest/collections/community/general/onepassword_lookup.html) —
  the 1Password CLI-based lookup plugin, for use without a Connect Server.

## License

MIT — see [LICENSE](LICENSE).

[docs_badge]: https://img.shields.io/badge/docs-brightgreen.svg
[docs_link]: https://galaxy.ansible.com/ui/repo/published/tigattack/onepassword_connect/docs/
[galaxy_ver_badge]: https://img.shields.io/ansible/collection/v/tigattack/onepassword_connect?label=version
[galaxy_dls_badge]: https://img.shields.io/ansible/collection/d/tigattack/onepassword_connect?label=downloads
[galaxy_link]:  https://galaxy.ansible.com/ui/repo/published/tigattack/onepassword_connect/
