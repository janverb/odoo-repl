This is a wrapper around the `bin/python_odoo` script included with Odoo buildouts. It adds some niceties to make debugging less painful.

# Running

First, install the wrapper. It's not on PyPi yet, so instead, run this in the repo directory:

```
$ pip3 install --user -e .
```

You can use it for both Python 2 and Python 3 after that, no need to install it twice. Though I've only ever tested it with Odoo 8, so chances are that it'll fail on any other Odoo version. It's still a work in progress.

A script is installed in `~/.local/bin`. Make sure that it's in your PATH.

To launch, run `odoo-repl -d <database name>`.

# Features

## Pretty-printing

Instead of seeing an uninformative `res.country(1,)` in your terminal, you get to look at something like this:

```
res.country[1]
__last_update:     '2019-31-10 12:00:00'
address_format:    u'%(street)s\n%(street2)s\n%(city)s %(state_code)s %(zip)s\n%(country_name)s'
code:              u'AD'
country_group_ids: res.country.group[]
create_date:       '2019-31-10 12:00:00'
create_uid:        res.users[1]
currency_id:       res.currency[1]
display_name:      u'Andorra'
id:                1
image:             'iVBORw0KGgoAAAANSUhEUgAAAPoAAACvCAYAAADUr8N5AAAABmJLR0QA/wD/AP+gvaeTAAAAB3RJ\nTUUH2wMJBAgMSOMd6QAAIABJREFUeJzt3XmQXddh3...
name:              u'Andorra'
write_date:        '2019-31-10 12:00:00'
write_uid:         res.users[1]
```

It even comes with colors.

## Easy model and record access

Normally you would have to type `session.env['res.country'].browse(1)` to access a record. You can type `env.res.country[1]` instead. Even better, it's tab-completed, so you often don't have to type out the full name. You can type `env.res.cou` and press TAB.

If you have a form URL you can run it through `browse` to extract the record. For example, `browse('http://localhost:8069/web#id=1&view_type=form&model=res.country')` returns the country record from before.

You can access users as attributes on `u`, e.g. `u.admin`. This is also tab-completed. That makes it easier to `.sudo()` as particular users.

You can access data records under `data`, e.g. `data.user_root`. THis doesn't handle namespacing nicely yet.
