
" indentation (usually 4 spaces)
set tabstop=4 softtabstop=4 shiftwidth=4 expandtab
autocmd FileType markdown           set shiftwidth=2 softtabstop=2 tabstop=2
autocmd FileType javascript         set shiftwidth=2 softtabstop=2 tabstop=2
autocmd FileType makefile           set noexpandtab

" line wrapping (https://vim.fandom.com/wiki/Automatic_word_wrapping)
set textwidth=79 formatoptions+=t formatoptions-=l

" search settings
set ignorecase smartcase

" special functions for markdown titles and lists
function! TogglePlusMinus()
    let save_pos = getpos('.')
    " move to start and yank first letter
    normal! ^"-yl
    " replace with opposite symbol
    if @- =~# '+'
        execute "normal! r-"
    elseif @- =~# '-'
        execute "normal! r+"
    endif
    call setpos('.', save_pos)
endfunction
nnoremap - :call TogglePlusMinus()<CR>

function! Underline()
    let save_pos = getpos('.')
    " move to beginning of next line and yank into comparison register
    normal j"-yy
    " promote line if it is an underline, else create one
    if @- =~# '-----*'
        execute "s/-/=/g"
    elseif @- =~# '=====*'
        normal "-dd
    else
        " move back up, duplicate line, and convert into dashes
        call setpos('.', save_pos)
        normal "-yy"-p
        execute "s/./-/g"
    endif
    execute "noh"
    call setpos('.', save_pos)
endfunction
nnoremap _ :call Underline()<CR>

" CODE FOLDING
set foldlevelstart=2
function! ToggleTOC()
    if foldclosed(v:lnum) >= 0
        normal! zR
    else
        normal! zM
    endif
    call SmartZZ()
endfunction
nnoremap z<Return> :call ToggleTOC()<CR>

function! MarkdownFolds()
    let thisline = getline(v:lnum)
    let nextline = getline(v:lnum+1)
    if match(nextline, '^=====*$') >= 0
        return ">1"
    elseif match(nextline, '^------*$') >= 0
        return ">1"
    elseif match(thisline, '^---$') >= 0
        return ">1"
    elseif match(thisline, '^##*') >= 0
        return ">1"
    else
        return "="
    endif
endfunction
" uhh, how does this work without my fold text function? oh well, it works!
autocmd FileType markdown,rmd,rmarkdown setlocal foldmethod=expr
autocmd FileType markdown,rmd,rmarkdown setlocal foldexpr=MarkdownFolds()
autocmd FileType markdown               setlocal foldtext=MarkdownFoldText()

function! ProgrammingFolds()
    let p2line = getline(v:lnum-2)
    let p1line = getline(v:lnum-1)
    if match(p1line, '^ *$') >= 0 && match(p2line, '^ *$') >= 0
        return ">1"
    else
        return "="
    endif
endfunction
" uhh, how does this work without my fold text function? oh well, it works!
autocmd FileType rust,python setlocal foldmethod=expr
autocmd FileType rust,python setlocal foldexpr=ProgrammingFolds()
autocmd FileType javascript,javascriptreact setlocal foldmethod=expr
autocmd FileType javascript,javascriptreact setlocal foldexpr=ProgrammingFolds()

function! LaTeXFolds()
    let thisline = getline(v:lnum)
    if match(thisline, '^% % %') >= 0
        return ">1"
    elseif match(thisline, '^\\part') >= 0
        return ">1"
    elseif match(thisline, '^\\chapter') >= 0
        return ">1"
    elseif match(thisline, '^\\section') >= 0
        return ">1"
    elseif match(thisline, '^\\subsection') >= 0
        return ">1"
    elseif match(thisline, '^\\begin{frame}') >= 0
        return ">1"
    elseif match(thisline, '^\\subsubsection') >= 0
        return ">2"
    else
        return "="
    endif
endfunction

function! LaTeXFoldText()
    let foldsize = (v:foldend-v:foldstart)
    return getline(v:foldstart).getline(v:foldstart+1).' ('.foldsize.' lines)'
endfunction

autocmd FileType tex setlocal foldmethod=expr
autocmd FileType tex setlocal foldexpr=LaTeXFolds()
autocmd FileType tex,python setlocal foldtext=LaTeXFoldText()

set spelllang=en_au
set spell!
" spellcheck: fix unreadable spelling recommendations
hi clear SpellLocal
hi SpellLocal ctermbg=Blue
set spelllang=en_gb

" Try to enable true color support (reads guibg correctly)
if (has("termguicolors"))
  set termguicolors
endif

" code folding colours
hi Folded guifg=#ff55ff guibg=NONE

hi CursorLine guibg=Grey8
hi CursorColumn guibg=Grey8
set cursorline
set cursorcolumn
