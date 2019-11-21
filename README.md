This is a wrapper around the `bin/python_odoo` script included with Odoo buildouts. It adds some niceties to make debugging less painful.

# Running

First, install the wrapper. It's not on PyPI yet, so instead, run this in the repo directory:

```
$ pip3 install --user -e .
```

You can use it for both Python 2 and Python 3 after that, no need to install it twice. It has been tested with Odoo 8, 10 and 12, but there might still be version-specific bugs. It doesn't work with versions below 8.

A script is installed in `~/.local/bin`. Make sure that it's in your PATH.

To launch, run `odoo-repl -d <database name>`.

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

Normally you would have to type `session.env['res.country'].browse(1)` to access a record. You can type `res.country[1]` instead. Even better, it's tab-completed, so you often don't have to type out the full name. You can type `res.cou` and press TAB.

You can access users as attributes on `u`, e.g. `u.admin`. This is also tab-completed. That makes it easier to `.sudo()` as particular users.

You can access data records as attributes on `ref`, e.g. `ref.base.user_root`. You can also call it the same way as `env.ref`, e.g. `ref('base.user_root')`.

Models have an added `_` method for quick and dirty searching. To find all countries that use EUR as a currency, you could run `res.country._(currency_id=ref.base.EUR)`. That saves you some typing compared to `session.env['res.country'].search([('currency_id', '=', session.env.ref('base.EUR').id)])`.

If you have a form URL you can run it through `browse` to extract the record. For example, `browse('http://localhost:8069/web#id=1&view_type=form&model=res.country')` returns the country record from before.
