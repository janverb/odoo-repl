# Link/copy to ~/.config/fish/completions/odoo-repl.fish

function __odoo_repl_complete_odoo_database
    psql -AtqwlF \t 2>/dev/null | awk 'NF > 1 { print $1 }'
end

complete -c odoo-repl -s d -l database -a '(__odoo_repl_complete_odoo_database)' -x -d "Database name"
complete -c odoo-repl -s h -l help -d "Show help"
complete -c odoo-repl -s c -l command -x -d "Initial command to execute"
complete -c odoo-repl -l ipython -d "Use IPython instead of the default REPL"
complete -c odoo-repl -s a -l args -x -d "Extra flags to pass to the interpreter"
complete -c odoo-repl -l interpreter -r -d "Use another interpreter"
complete -c odoo-repl -l no-interactive -d "Immediately quit odoo-repl after starting"
complete -c odoo-repl -l run-tests -d "Run tests, then exit"
