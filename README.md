`odoo-repl` improves Odoo's interactive shell. It's a development tool, useful for debugging and documentation. It lets you do many things that you might otherwise use `psql` and `grep` for.

It's compatible with Odoo 8 and upwards.

# Installing

The module can be installed with pip: `pip install odoo-repl`.

# Running

The most basic way to enable it is to run

```python
import odoo_repl; odoo_repl.enable()
```

when in an ordinary `odoo-bin shell` environment or similar. That will make all the features available.

## pdb

You can also use it to enhance `pdb`. Instead of setting a breakpoint with `import pdb; pdb.set_trace()`, write `import odoo_repl; odoo_repl.set_trace()`.

## Buildout

If you use the [Odoo buildout recipe](http://docs.anybox.fr/anybox.recipe.odoo/current/) you can instead launch it using the `odoo-repl` wrapper script, which invokes `python_odoo` and does the basic setup for you. The script is automatically installed when you install the module using `pip`.

Run `odoo-repl` in the buildout directory to launch it. To pick a specific database, run `odoo-repl -d <database name>`.

Run `odoo-repl --help` to see a full list of options.

# Overview

`odoo-repl` is useful for a few different things:

- Documentation. View summaries of models, fields, methods, and more, including where they were defined and how they are overridden.

- Experimentation. Debug your code by calling methods directly, without the web interface getting in the way.

- Exploring records. Get overviews of all a record's fields, run quick and dirty searches, and refer to records with convenient syntax.

It adds its own methods to many objects. Those generally end with an underscore, e.g. `.source_()` rather than `.source()`, to avoid conflicts with existing names.

# Documentation

All models are made available as ordinary names. That means that you can type `res.currency` instead of `env['res.currency']`, and that you can press tab to complete model names.

You can type the name of something to get a summary of it. This works for models, fields, and methods.

## Models

```pycon
>>> res.currency
res.currency
Currency

base:
 sd  active:                 boolean
 s   currency_subunit_label: char (Currency Subunit)
 s   currency_unit_label:    char (Currency Unit)
   c date:                   date
 s c decimal_places:         integer
Rs   name:                   char (Currency)
 sd  position:               selection (Symbol Position)
   c rate:                   float (Current Rate)
 s   rate_ids:               one2many: res.currency.rate (Rates)
 sd  rounding:               float (Rounding Factor)
Rs   symbol:                 char

base: [...]/odoo/addons/base/models/res_currency.py:23
```

This is an overview all the fields that are defined on the model.

Each field is marked with `r`, `s`, `d`, and `c`, which stand for `required`, `stored`, `default` and `computed`, respectively. You can use this to quickly tell which fields you should expect to find in the database, which ones may not have a value, and so on.

A field that's marked as `R` is required and doesn't have a default, so you should usually pass it to `.create()`.

The module and file where the model was defined are listed at the bottom. `odoo-repl` tries to provide that information whenever possible.

## Fields

That model overview doesn't tell you everything. You can get more detailed information about a field:

```pycon
>>> res.currency.rounding
float rounding on res.currency (store, related_sudo)
Rounding Factor
Default value: 0.01
base: [...]/odoo/addons/base/models/res_currency.py:34
```

You can find information about the default value, whether/how it's computed, and where it was defined.

## Methods

The same thing works for methods:

```pycon
>>> res.currency.round
@api.multi
res.currency.round(self, amount)
Return ``amount`` rounded  according to ``self``'s rounding rules.

:param float amount: the amount to round
:return: rounded float

base: [...]/odoo/addons/base/models/res_currency.py:130
```

You can see the signature, decorators, docstrings, and everywhere it was defined. This is especially useful to track how different modules override the same method.

## Source code

It's often helpful to look at the actual source code instead of these summaries. For that, all of these objects have a `.source_()` method. Run that to get the source code printed to your screen:

```pycon
>>> res.currency.rounding.source_()
base: [...]/odoo/addons/base/models/res_currency.py:34
rounding = fields.Float(string='Rounding Factor', digits=(12, 6), default=0.01)
```

You can also call the `.edit_()` method to launch a text editor at the right file and line. The editor is taken from your `$EDITOR` environment variable, or `nano` by default.

# Working with records

If you know a record's ID you can refer to it concisely, e.g. `res.currency[1]`. This representation is used throughout `odoo-repl`, so often you can just copy/paste it. You can also create a recordset this way, e.g. `res.currency[1, 2, 3]`.

## Summaries

A record is printed with the value of all its fields:

```pycon
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
rate_ids:               res.currency.rate[129] (ref.base.rateEUR)
rounding:               0.01
symbol:                 '€'

Written on 2020-02-11 13:00:34

base: [...]/odoo/addons/base/data/res_currency_data.xml:1166
```

If possible, you're also told when and by whom it was created and modified.

If the record has a XML ID you're told what it is (`base.EUR` in this case) as well as the files where it was defined.

## Methods

You can call `.open_()` to open the record in your browser.

`.source_()` and `.edit_()` work on records that were defined in XML files.

## Shorthand

Instead of writing `env.ref('base.EUR')`, you can write `ref.base.EUR`. This has tab completion.

To get the `res.users` record for `admin`, you can write `u.admin`, or `u['admin']`. That shorthand works with any username. It's useful for trying things as different users, e.g. `.sudo(u.demo)`.

## Searching

Models have a method called `_` that works as a more flexible version of `search`. It supports keyword arguments and it's less fussy about record IDs and delimiters.

Let's say that you want to find the `res.country` record for Belgium. Normally you'd have to write `env['res.country'].search([("name", "=", "Belgium")])`, which is pretty verbose. The shortest way to write it with `odoo-repl` is `res.country._(name="Belgium")`.

You can use another operator by putting its name after a double underscore (`__`). To find all countries that *aren't* Belgium, use `res.country._(name__ne="Belgium")`.

The same trick can be used for dotted paths across relations: `res.users._(partner_id__name="Mitchell Admin")`.

Alternatively you can write it as a flat list of arguments without any brackets, i.e. `res.users._("partner_id.name", "=", "Mitchell Admin")`.

When using records in a `.search()` domain you have to explicitly take the ID of the record instead of passing the record. `._()` doesn't need that, so `res.partner._(country_id=ref.base.be)` will just work.

## Getting a random record

The `.shuf_()` method (short for "shuffle") gives you a random record. If you want to look at a random user record, just run `res.users.shuf_()`.

It takes an optional argument for the number of records to return. `res.users.shuf_(10)` will return a recordset with ten random users.

## Operating on all records

Iterating over a model will iterate over all its records. So if you want to run a piece of code on all users, just start with `for user in res.users:`. This is equivalent to `for user in env['res.users'].search([]):`, just shorter.

The `.mapped()` and `.filtered()` methods on models also operate on all records, saving you from typing `.search([])`.

# More model information

Besides the summaries, there are methods to get more information about a model.

## Listing menus

The `.menus_()` method prints ways the model can be reached in the web interface, with information about the views that are used. For example:

```pycon
>>> project.task.menus_()
Project/All Tasks (ref.project.action_view_task)
    calendar: ir.ui.view[591] (ref.project.view_task_calendar)
    form: ir.ui.view[588] (ref.project.view_task_form2)
    graph: ir.ui.view[593] (ref.project.view_project_task_graph)
    kanban: ir.ui.view[589] (ref.project.view_task_kanban)
    pivot: ir.ui.view[592] (ref.project.view_project_task_pivot)
    timeline: ir.ui.view[1094] (ref.project_timeline.project_task_timeline)
    tree: ir.ui.view[590] (ref.project.view_task_tree2)
    activity: ???

res.users → Assigned Tasks (ref.project.act_res_users_2_project_task_opened)
    calendar: ir.ui.view[591] (ref.project.view_task_calendar)
    form: ir.ui.view[588] (ref.project.view_task_form2)
    graph: ir.ui.view[593] (ref.project.view_project_task_graph)
    tree: ir.ui.view[590] (ref.project.view_task_tree2)
```

On this instance, you can reach `project.task` by navigating to the "All Tasks" submenu in "Project", and you can choose from a number of different view types. They all have their `ir.ui.view` record listed, except for the `activity` type, for which it couldn't be determined.

You can also reach tasks from the "Assigned Tasks" menu on `res.users` records.

## Security information

`.rules_()` prints all the `ir.model.access` and `ir.rule` records that apply to a model:

```pycon
>>> account.invoice.rules_()
ir.model.access[355] (ref.account.access_account_invoice_uinvoice)
account.invoice
Billing (ref.account.group_account_invoice)
read, write, create, unlink

[...]

ir.model.access[543] (ref.purchase.access_account_invoice_purchase_manager)
account_invoice purchase manager
Manager (ref.purchase.group_purchase_manager)
read,      ,       ,

[...]

ir.rule[68] (ref.account.invoice_comp_rule)
Invoice multi-company
Everyone
read, write, create, unlink
['|',
 ('company_id', '=', False),
 ('company_id', 'child_of', [user.company_id.id])]
account: [...]/addons/account/security/account_security.xml:126
```

You still need a good grasp of how `ir.model.access` and `ir.rule` work to interpret this information correctly.

## Rendering views

`.view_()` prints the XML used to render a model's view. By default, it prints the form view, but you can pass an argument to render a different view.

```pycon
>>> account.invoice.view_('tree')

<tree decoration-info="state == 'draft'" decoration-muted="state == 'cancel'" decoration-bf="not partner_id" string="Vendor Bill" js_class="account_bills_tree">
  <field name="partner_id" invisible="1"/>
[...]
  <field name="company_currency_id" invisible="1"/>
  <field name="state"/>
  <field name="type" invisible="context.get('type',True)"/>
</tree>
```

## Listing methods

`.methods_()` lists all the methods that are defined for the model, grouped by implementing module.

Methods that are available on all models are only shown if they're overridden.

```pycon
>>> res.partner.methods_()
project
_compute_task_count(self)

partner_multi_relation_tabs
_add_tab_pages(self, view)
_compute_tabs_visibility(self)
_get_tabs(self)
add_field(self, tab)
browse(self, arg=None, prefetch=None)
fields_view_get(self, view_id=None, view_type='form', toolbar=False, submenu=False)

[...]
```

## SQL information

`.sql_()` shows a very basic summary of a model's table in the database.

```pycon
>>> res.currency.sql_()
res_currency
accuracy:    int4
active:      bool
base:        bool
company_id:  int4
create_date: timestamp
create_uid:  int4
date:        date
id:          int4
name:        varchar
position:    varchar
rounding:    numeric
symbol:      varchar
write_date:  timestamp
write_uid:   int4
```

# Integrations

`odoo-repl` can integrate with a few external programs.

## git

Objects with a `.source_()` method also have a `.gitsource_()` method that generates URLs for git repositories. This is useful for sharing with other people.

If possible it will create a URL with a commit hash in it, so it will stay the same even if the repository is updated.

```pycon
>>> res.users._browse.gitsource_()
base_suspend_security: https://github.com/OCA/server-backend/blob/92ebf2d/base_suspend_security/models/res_users.py#L11
BaseModel: https://github.com/oca/ocb/blob/f228ae0ce5e/odoo/models.py#L4675
```

Although only Github links are shown here it also works with other hosts that are similar enough to Github, like private Gitlab instances.

## grep

Objects with a `.source_()` method have a `.grep_()` method for running `grep` on their source code. This is especially useful when they are defined across many different modules.

You can also use the `grep_()` function to search through all installed modules and Odoo's code (while skipping modules that aren't installed).

[`ripgrep`](https://github.com/BurntSushi/ripgrep) (`rg`) is used instead of `grep` if it's installed. This is recommended because it has more readable output when searching multiple files and handles directory searches better.

Keyword arguments are converted to flags. To get one line of context you'd pass `-C 1` to `grep`. You can get the same by passing `C=1` to `.grep_()`.

```pycon
>>> ir.attachment.grep_("ValidationError", C=1)
[...]/odoo/addons/base/models/ir_attachment.py
14-from odoo import api, fields, models, tools, SUPERUSER_ID, exceptions, _
15:from odoo.exceptions import AccessError, ValidationError
16-from odoo.tools import config, human_size, ustr, html_escape
--
334-            if not any([has_group(g) for g in self.get_serving_groups()]):
335:                raise ValidationError("Sorry, you are not allowed to write on this document")
336-
--
564-            except Exception:
565:                raise exceptions.ValidationError(_("ERROR: Invalid PDF file!"))
566-            max_page = input_pdf.getNumPages()
--
606-        if 'pdf' not in self.mimetype:
607:            raise exceptions.ValidationError(_("ERROR: the file must be a PDF"))
608-        if indices:
--
611-            except ValueError:
612:                raise exceptions.ValidationError(_("ERROR: Invalid list of pages to split. Example: 1,5-9,10"))
613-            return self._split_pdf_groups(pdf_groups=[[min(x), max(x)] for x in pages], remainder=remainder)

```

## fzf

[`fzf`](https://github.com/junegunn/fzf) is a tool for fuzzy incremental searching. If you have it installed you can use it to search through records very easily.

You can use the `.fzf_()` method on a model to search through display names:

```pycon
>>> ir.ui.menu.fzf_()

> Settings/Technical/Parameters/System Parameters
  1/71
> syste

ir.ui.menu[25] (ref.base.ir_config_menu)
Settings/Technical/Parameters/System Parameters
action:        ir.actions.act_window[10] (ref.base.ir_config_list_action)
active:        True
[...]
```

Or you can use it on a field to search through the values of that field instead:

```pycon
>>> ir.ui.view.arch.fzf_()

  ..arding_state') in ('done', 'just_done') else '') + (' o_onboarding_steps_just_done' if sta..
  ..1"/>                                     <field name="sub_model_object_field" domain="[('m..
  .., [])]}" class="btn btn-primary float-right" name="channel_join_and_get_info">Join</button..
> ..eld name="exclude_contact"/>                             <field name="exclude_journal_item..
  50/346
> exclude_jo

ir.ui.view[96] (ref.base.base_partner_merge_automatic_wizard_form)
base.partner.merge.automatic.wizard.form
active:               True
[...]
```

It's hard to get it across in text, so it may be best to just try it.

# Odoo addons

Odoo addons/modules can be inspected interactively, as attributes of the `addons` object:

```pycon
>>> addons.base_suspend_security
base_suspend_security 12.0.1.0.1 by Therp BV, brain-tec AG, Odoo Community Association (OCA)
http://localhost:8012/web?debug=1#model=ir.module.module&id=382
[...]/server-backend/base_suspend_security
Installed
Suspend security
Suspend security checks for a call
Depends: base
Dependents: base_user_role_history
Defines: base, ir.model.access, ir.rule, res.users

[...]

This module was written to  allow you to call code with some `uid` while being sure no security checks (`ir.model.access` and `ir.rule`) are done. In this way, it's the same as `sudo()`, but the crucial difference is that the code still runs with the original user id. This can be important for inherited code that calls workflow functions, subscribes the current user to some object, etc.

[...]
```

In addition to the contents of the `README`, you see its dependencies, the modules that depend on it, the models it defines, a link to it in the web interface, and its location in the filesystem.

These objects have a few useful methods and attributes. The `manifest` attribute gives you the addon's manifest:

```pycon
>>> addons.base_suspend_security.manifest
{'application': False,
 'author': 'Therp BV, brain-tec AG, Odoo Community Association (OCA)',
 'auto_install': False,
 'category': 'Hidden/Dependency',
[...]
>>> addons.base_suspend_security.manifest.version
'12.0.1.0.1'
```

`record` gives you the `ir.module.module` record of the addon.

`.open_()` opens the module in the web interface in your browser.

`.grep_()` and `.gitsource_()` are supported.

`.definitions_()` prints a listing of every model, field, method and record defined in the addon.
