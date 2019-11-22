This is a wrapper around the `bin/python_odoo` script included with Odoo buildouts. It adds some niceties to make debugging less painful.

# Running

First, install the wrapper. It's not on PyPI yet, so instead, run this in the repo directory:

```
$ pip3 install --user -e .
```

You can use it for both Python 2 and Python 3 after that, no need to install it twice. It has been tested with Odoo 8, 10 and 12, but there might still be version-specific bugs. It doesn't work with versions below 8.

A script is installed in `~/.local/bin`. Make sure that it's in your PATH.

To launch, run `odoo-repl -d <database name>` in the buildout directory.

Run `odoo-repl --help` to see a full list of options.

## Without buildout

It's also possible to enable the package's features in an ordinary `odoo-bin shell` session. To do that, execute `import odoo_repl` and then `odoo_repl.enable(env)`. To do it this way it does need to be installed for your current Python version or virtualenv.

# Features

## Pretty-printing

Instead of seeing an uninformative `res.country(1,)` in your terminal, you get to look at something like this:

```
res.country[1] (ref.base.ad)
address_format:    '%(street)s\n%(street2)s\n%(city)s %(state_code)s %(zip)s\n%(country_name)s'
address_view_id:   ir.ui.view[]
code:              'AD'
country_group_ids: res.country.group[]
currency_id:       res.currency[1]
display_name:      'Andorra'
image:             b'iVBORw0KGgoAAAANSUhEUgAAAPoAAACvCAYAAADUr8N5AAAABmJLR0QA/wD/AP+gvaeTAAAAB3RJTUUH2wMJBAgMSOMd6QAAIABJREFUeJzt3XmQXddh3/...
name:              'Andorra'
name_position:     'before'
phone_code:        376
state_ids:         res.country.state[]
vat_label:         False
```

It even comes with colors, unless you pass the `--no-color` flag to `odoo-repl`.

## Easy record access

You can just type `res.country[1]` instead of `session.env['res.country'].browse(1)`. Even better, it's tab-completed, so you often don't have to type out the full name. You can type `res.cou` and press TAB.

You can access users by their usernames as attributes on `u`, e.g. `u.admin`. This is also tab-completed. That makes it easier to `.sudo()` as particular users. You can also use indexing syntax for more complicated usernames, e.g. `u['test@example.com']`.

You can access data records as attributes on `ref`, e.g. `ref.base.user_root`. You can also call it like a function, e.g. `ref('base.user_root')`.

The `search` and `create` methods make intelligent use of keyword arguments:

- To find all countries that use EUR as a currency, run `res.country.search(currency_id=ref.base.EUR)`.

- To create a new country that uses USD as a currency, run `res.country.create(name="Odooland", currency_id=ref.base.USD)`.

If you have a form URL you can run it through `browse` to extract the record. For example, `browse('http://localhost:8069/web#id=1&view_type=form&model=res.country')` returns `res.country[1]`.
