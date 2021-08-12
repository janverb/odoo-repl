# Link/copy to ~/.config/fish/completions/odoo-repl.fish

complete -c odoo-repl -s h -l help -d "Show help"
complete -c odoo-repl -s c -l command -x -d "Initial command to execute"
complete -c odoo-repl -l no-interactive -d "Immediately quit odoo-repl after starting"
complete -c odoo-repl -l run-tests -d "Run odoo-repl's own tests, then exit"
complete -c odoo-repl -s s -l with-server -d "Run the web server in the background"
