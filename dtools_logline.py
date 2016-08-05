import os
import subprocess
import re
import codecs
import tempfile
import time
import logging

import sublime, sublime_plugin

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
logger.addHandler(logging.StreamHandler())

class DtoolsLoglineCommand(sublime_plugin.WindowCommand):
    def run(self):
        logger.debug('logline run')
        self.view = self.window.active_view()
        if not self.view:
            sublime.set_timeout(self.run, 1)
            return
        (row,col) = self.view.rowcol(self.view.sel()[0].begin())
        row = row + 1
        col = col + 1
        logger.debug('row, col is ' + str(row) + ', ' + str(col))
        ViewCollection.log_line(self.view, row)


def git_file_path(view, git_path):
    if not git_path:
        return False
    full_file_path = os.path.realpath(view.file_name())
    git_path_to_file = full_file_path.replace(git_path, '').replace('\\', '/')
    if git_path_to_file[0] == '/':
        git_path_to_file = git_path_to_file[1:]
    return git_path_to_file


def git_root(directory):
    if os.path.exists(os.path.join(directory, '.git')):
        return directory
    else:
        parent = os.path.realpath(os.path.join(directory, os.path.pardir))
        if parent == directory:
            # we have reached root dir
            return False
        else:
            return git_root(parent)

def git_tree(view):
    full_file_path = view.file_name()
    file_parent_dir = os.path.realpath(os.path.dirname(full_file_path))
    return git_root(file_parent_dir)


def git_dir(directory):
    if not directory:
        return False
    pre_git_dir = os.path.join(directory, '.git')
    if os.path.isfile(pre_git_dir):
        submodule_path = ''
        with open(pre_git_dir) as submodule_git_file:
            submodule_path = submodule_git_file.read()
            submodule_path = os.path.join('..', submodule_path.split()[1])

            submodule_git_dir = os.path.abspath(
                os.path.join(pre_git_dir, submodule_path))

        return submodule_git_dir
    else:
        return pre_git_dir

class ViewCollection:
    views = {} # Todo: these aren't really views but handlers. Refactor/Rename.
    git_times = {}
    git_files = {}
    buf_files = {}

    @staticmethod
    def add(view):
        key = ViewCollection.get_key(view)
        handler = ViewCollection.views[key] = Handler(view)
        handler.reset()
        return handler

    @staticmethod
    def git_path(view):
        key = ViewCollection.get_key(view)
        if key in ViewCollection.views:
            return ViewCollection.views[key].get_git_path()
        else:
            return False

    @staticmethod
    def get_key(view):
        return view.file_name()

    @staticmethod
    def has_view(view):
        key = ViewCollection.get_key(view)
        return key in ViewCollection.views

    @staticmethod
    def get_handler(view):
        if ViewCollection.has_view(view):
            key = ViewCollection.get_key(view)
            return ViewCollection.views[key]
        else:
            return ViewCollection.add(view)

    @staticmethod
    def log_line(view, linenumber):
        return ViewCollection.get_handler(view).log_line(linenumber)      

    @staticmethod
    def tmp_file():
        fd, filepath = tempfile.mkstemp(prefix='git_gutter_')
        os.close(fd)
        return filepath

    @staticmethod
    def git_tmp_file(view):
        key = ViewCollection.get_key(view)
        if not key in ViewCollection.git_files:
            ViewCollection.git_files[key] = ViewCollection.tmp_file()
        return ViewCollection.git_files[key]

class Handler:

    def __init__(self, view):
        self.load_settings()
        self.view = view
        self.git_temp_file = ViewCollection.git_tmp_file(self.view)
        self.git_tree = None
        self.git_dir = None
        self.git_path = None

    def on_disk(self):
        # if the view is saved to disk
        on_disk = self.view.file_name() is not None
        if on_disk:
            self.git_tree = self.git_tree or git_tree(self.view)
            self.git_dir = self.git_dir or git_dir(self.git_tree)
            self.git_path = self.git_path or git_file_path(
                self.view, self.git_tree
            )
        return on_disk

    def reset(self):
        if self.on_disk() and self.git_path and self.view.window():
            self.view.window().run_command('git_gutter')

    def get_git_path(self):
        return self.git_path

    def log_line(self, linenumber):
        args = [
            self.git_binary_path,
            '--git-dir=' + self.git_dir,
            '--work-tree=' + self.git_tree,
            'log',
            '-L' + str(linenumber) + ',+1:' + self.git_path
        ]
        logger.debug('log_line' + str(args))
        try:
            contents = self.run_command(args)
            contents = contents.replace(b'\r\n', b'\n')
            contents = contents.replace(b'\r', b'\n')
            logger.debug('content is ' + contents.decode('utf-8'))
            logger.debug('writing content to ' + self.git_temp_file)
            with open(self.git_temp_file, 'wb') as f:
                f.write(contents)
            view = self.view.window().open_file(self.git_temp_file)
            def set_syntax():
                if view.is_loading():
                    sublime.set_timeout_async(set_syntax, 0.1)
                else:
                    view.set_syntax_file('Packages/Git/syntax/Git Commit View.tmLanguage')
            set_syntax()
        except Exception:
            logger.exception("fail log git")
            pass

    def run_command(self, args):
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        proc = subprocess.Popen(args, stdout=subprocess.PIPE,
                                startupinfo=startupinfo, stderr=subprocess.PIPE)
        return proc.stdout.read()

    def load_settings(self):
        self.settings = sublime.load_settings('GitGutter.sublime-settings')
        self.user_settings = sublime.load_settings(
            'Preferences.sublime-settings')

        # Git Binary Setting
        self.git_binary_path = 'git'
        git_binary = self.user_settings.get(
            'git_binary') or self.settings.get('git_binary')
        if git_binary:
            self.git_binary_path = git_binary
