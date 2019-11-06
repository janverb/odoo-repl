# Append to your .bashrc

_odoo_databases()
{
    # Copied from /usr/share/bash-completions/completions/psql
    COMPREPLY=( $( compgen -W "$( psql -XAtqwlF $'\t' 2>/dev/null | \
        awk 'NF > 1 { print $1 }' )" -- "$cur" ) )
}

_odoo-repl()
{
    local cur prev words cword split
    _init_completion -s || return

    case $prev in
        -d|--database)
            _odoo_databases
            return
            ;;
        -c|--command|-a|--args)
            return
            ;;
        *)
            _filedir
            return
            ;;
    esac
}

complete -F _odoo-repl odoo-repl
