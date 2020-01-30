`odoo-repl` is an interactive development environment for Odoo. It makes it easier to work with records and has basic code analysis features that can make you reach for `grep` less often.

# Installing

This module is not on PyPi yet, but it can be installed with pip, by running this in the repository directory:

```
pip install -e .
```

# Running

The most basic way to enable it is to run

```python
import odoo_repl; odoo_repl.enable()
```

when in an ordinary `odoo-bin shell` environment or similar.

## Buildout

If you use the [Odoo buildout recipe](http://docs.anybox.fr/anybox.recipe.odoo/current/) you can instead launch it using the `odoo-repl` wrapper script, which saves a little typing and is useful for older versions of Odoo that don't have the `shell` subcommand. The script is automatically installed when you install the module using `pip`.

Run `odoo-repl` in the buildout directory to launch it. To pick a specific database, run `odoo-repl -d <database name>`.

Run `odoo-repl --help` to see a full list of options.

# Features

This is an overview of a few of the most useful features, but it's not an exhaustive list.

## Easier model access

Normally you have to type something along the lines of `self.env["res.currency"].browse(1)` to get a record. `odoo-repl` streamlines that to `res.currency[1]`.

`self.env.ref("base.group_public")` becomes `ref.base.group_public`. This has tab completion.

`self.env["res.users"].search([("login", "=", "admin")])` becomes `u.admin`, also with tab completion. `u["admin"]` works too, useful for more complicated usernames.

`self.env["res.country"].search([("currency_id", "=", somerecord.id)])` becomes `res.country._(currency_id=somerecord)`. It can also be spelled `res.country._("currency_id", "=", somerecord.id)` or `res.country._([("currency_id", "=", somerecord.id)])`. All the normal arguments of `search` are supported.

`self.env["res.country"].search([]).filtered(...)` becomes `res.country.filtered(...)`. Methods like `filtered` and `mapped` implicitly operate on all records.

## Record overviews

Instead of seeing an uninformative `res.currency(1,)` in your terminal, you get to look at something like this:

```pycon
>>> res.currency[1]
res.currency[1] (ref.base.EUR)
EUR
active:                 True
currency_subunit_label: 'Cents'
currency_unit_label:    'Euros'
date:                   2010-01-01
decimal_places:         2
name:                   'EUR'
position:               'after'
rate:                   1.0
rate_ids:               res.currency.rate[129]
rounding:               0.01
symbol:                 '€'

base: [...]/odoo/addons/base/data/res_currency_data.xml:1157
```

Because this record was defined from module data, it shows the XML ID in the notation from earlier, and it shows the module and file it was defined in at the bottom.

## Model overviews

You can also see summaries of models:

```pycon
>>> ir.attachment
ir.attachment
Attachment
 s   access_token:   char (Access Token)
 sd  active:         boolean (Active)
 s   checksum:       char (Checksum/SHA1)
 sd  company_id:     many2one: res.company (Company)
   c datas:          binary (File Content)
[...]
rsd  type:           selection (Type)
 s   url:            char (Url)

web_editor: [...]/addons/web_editor/models/ir_attachment.py:7
base: [...]/odoo/addons/base/models/ir_attachment.py:24
```

Fields:

```pycon
>>> ir.attachment.res_model
char res_model on ir.attachment (readonly, store, related_sudo)
Resource Model: The database object this attachment will be attached to.
base: [...]/odoo/addons/base/models/ir_attachment.py:292
```

And methods:

```pycon
>>> ir.attachment.get_serving_groups
@api.model
ir.attachment.get_serving_groups(self)
An ir.attachment record may be used as a fallback in the
http dispatch if its type field is set to "binary" and its url
field is set as the request's url. Only the groups returned by
this method are allowed to create and write on such records.

base: /home/jan/therp/buildout12/parts/odoo/odoo/addons/base/models/ir_attachment.py:279
```

Fields in the model overview can be marked with `r`, `s`, `d`, and `c`, which stand for `required`, `store`, `default` and `computed`, respectively.

These all support additional methods `.source_()`, `.grep_()`, and `.edit_()`. `.source_()` prints the source code of all definitions. `.grep_()` runs `grep` on the source code, even if it's spread out across multiple modules. `.edit_()` opens an editor at the definition, based on your `$EDITOR` environment variable.

```pycon
>>> ir.attachment.grep_("ValidationError")
[...]/odoo/addons/base/models/ir_attachment.py
15:from odoo.exceptions import AccessError, ValidationError
335:                raise ValidationError("Sorry, you are not allowed to write on this document")
565:                raise exceptions.ValidationError(_("ERROR: Invalid PDF file!"))
607:            raise exceptions.ValidationError(_("ERROR: the file must be a PDF"))
612:                raise exceptions.ValidationError(_("ERROR: Invalid list of pages to split. Example: 1,5-9,10"))
```
