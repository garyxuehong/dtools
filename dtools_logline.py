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

class DToolsLoglineCommand(sublime_plugin.WindowCommand):
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

class ViewCollection:
    views = {} # Todo: these aren't really views but handlers. Refactor/Rename.
    git_times = {}
    git_files = {}
    buf_files = {}
    compare_against = "HEAD"

    @staticmethod
    def add(view):
        key = ViewCollection.get_key(view)
        handler = ViewCollection.views[key] = GitGutterHandler(view)
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
    def diff(view):
        return ViewCollection.get_handler(view).diff()

    @staticmethod
    def log_line(view, linenumber):
        return ViewCollection.get_handler(view).log_line(linenumber)      

    @staticmethod
    def untracked(view):
        return ViewCollection.get_handler(view).untracked()

    @staticmethod
    def ignored(view):
        return ViewCollection.get_handler(view).ignored()

    @staticmethod
    def total_lines(view):
        return ViewCollection.get_handler(view).total_lines()

    @staticmethod
    def git_time(view):
        key = ViewCollection.get_key(view)
        if not key in ViewCollection.git_times:
            ViewCollection.git_times[key] = 0
        return time.time() - ViewCollection.git_times[key]

    @staticmethod
    def clear_git_time(view):
        key = ViewCollection.get_key(view)
        ViewCollection.git_times[key] = 0

    @staticmethod
    def update_git_time(view):
        key = ViewCollection.get_key(view)
        ViewCollection.git_times[key] = time.time()

    @staticmethod
    def tmp_file():
        '''
            Create a temp file and return the filepath to it.
            Caller is responsible for clean up
        '''
        fd, filepath = tempfile.mkstemp(prefix='git_gutter_')
        os.close(fd)
        return filepath

    @staticmethod
    def git_tmp_file(view):
        key = ViewCollection.get_key(view)
        if not key in ViewCollection.git_files:
            ViewCollection.git_files[key] = ViewCollection.tmp_file()
        return ViewCollection.git_files[key]

    @staticmethod
    def buf_tmp_file(view):
        key = ViewCollection.get_key(view)
        if not key in ViewCollection.buf_files:
            ViewCollection.buf_files[key] = ViewCollection.tmp_file()
        return ViewCollection.buf_files[key]

    @staticmethod
    def set_compare(commit):
        print("GitGutter now comparing against:",commit)
        ViewCollection.compare_against = commit

    @staticmethod
    def get_compare(view):
        compare = ViewCollection.compare_against or "HEAD"
        return view.settings().get('git_gutter_compare_against', compare)

    @staticmethod
    def current_branch(view):
        key = ViewCollection.get_key(view)
        return ViewCollection.views[key].git_current_branch()

    @staticmethod
    def show_status(view):
        key = ViewCollection.get_key(view)
        return ViewCollection.views[key].show_status

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

class GitGutterHandler:

    def __init__(self, view):
        self.load_settings()
        self.view = view
        self.git_temp_file = ViewCollection.git_tmp_file(self.view)
        self.buf_temp_file = ViewCollection.buf_tmp_file(self.view)
        self.git_tree = None
        self.git_dir = None
        self.git_path = None

    def _get_view_encoding(self):
        # get encoding and clean it for python ex: "Western (ISO 8859-1)"
        # NOTE(maelnor): are we need regex here?
        pattern = re.compile(r'.+\((.*)\)')
        encoding = self.view.encoding()
        if encoding == "Undefined":
            encoding = self.view.settings().get('default_encoding')
        if pattern.match(encoding):
            encoding = pattern.sub(r'\1', encoding)

        encoding = encoding.replace('with BOM', '')
        encoding = encoding.replace('Windows', 'cp')
        encoding = encoding.replace('-', '_')
        encoding = encoding.replace(' ', '')

        # work around with ConvertToUTF8 plugin
        origin_encoding = self.view.settings().get('origin_encoding')
        return origin_encoding or encoding

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

    def update_buf_file(self):
        chars = self.view.size()
        region = sublime.Region(0, chars)

        # Try conversion
        try:
            contents = self.view.substr(
                region).encode(self._get_view_encoding())
        except UnicodeError:
            # Fallback to utf8-encoding
            contents = self.view.substr(region).encode('utf-8')
        except LookupError:
            # May encounter an encoding we don't have a codec for
            contents = self.view.substr(region).encode('utf-8')

        contents = contents.replace(b'\r\n', b'\n')
        contents = contents.replace(b'\r', b'\n')

        with open(self.buf_temp_file, 'wb') as f:
            if self.view.encoding() == "UTF-8 with BOM":
                f.write(codecs.BOM_UTF8)

            f.write(contents)

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
            ViewCollection.update_git_time(self.view)
            self.view.window().open_file(self.git_temp_file)
        except Exception:
            logger.exception("fail log git")
            pass

    def update_git_file(self):
        # the git repo won't change that often
        # so we can easily wait 5 seconds
        # between updates for performance
        if ViewCollection.git_time(self.view) > 5:
            with open(self.git_temp_file, 'w'):
                pass

            args = [
                self.git_binary_path,
                '--git-dir=' + self.git_dir,
                '--work-tree=' + self.git_tree,
                'show',
                ViewCollection.get_compare(self.view) + ':' + self.git_path,
            ]
            try:
                contents = self.run_command(args)
                contents = contents.replace(b'\r\n', b'\n')
                contents = contents.replace(b'\r', b'\n')
                with open(self.git_temp_file, 'wb') as f:
                    f.write(contents)

                ViewCollection.update_git_time(self.view)
            except Exception:
                pass

    def total_lines(self):
        chars = self.view.size()
        region = sublime.Region(0, chars)
        lines = self.view.lines(region)
        return len(lines)

    # Parse unified diff with 0 lines of context.
    # Hunk range info format:
    #   @@ -3,2 +4,0 @@
    #     Hunk originally starting at line 3, and occupying 2 lines, now
    #     starts at line 4, and occupies 0 lines, i.e. it was deleted.
    #   @@ -9 +10,2 @@
    #     Hunk size can be omitted, and defaults to one line.
    # Dealing with ambiguous hunks:
    #   "A\nB\n" -> "C\n"
    #   Was 'A' modified, and 'B' deleted? Or 'B' modified, 'A' deleted?
    #   Or both deleted? To minimize confusion, let's simply mark the
    #   hunk as modified.
    def process_diff(self, diff_str):
        inserted = []
        modified = []
        deleted = []
        hunk_re = '^@@ \-(\d+),?(\d*) \+(\d+),?(\d*) @@'
        hunks = re.finditer(hunk_re, diff_str, re.MULTILINE)
        for hunk in hunks:
            start = int(hunk.group(3))
            old_size = int(hunk.group(2) or 1)
            new_size = int(hunk.group(4) or 1)
            if not old_size:
                inserted += range(start, start + new_size)
            elif not new_size:
                deleted += [start + 1]
            else:
                modified += range(start, start + new_size)
        return (inserted, modified, deleted)

    def diff(self):
        if self.on_disk() and self.git_path:
            self.update_git_file()
            self.update_buf_file()
            args = [
                self.git_binary_path, 'diff', '-U0', '--no-color', '--no-index',
                self.ignore_whitespace,
                self.patience_switch,
                self.git_temp_file,
                self.buf_temp_file,
            ]
            args = list(filter(None, args))  # Remove empty args
            results = self.run_command(args)
            encoding = self._get_view_encoding()
            try:
                decoded_results = results.decode(encoding.replace(' ', ''))
            except UnicodeError:
                try:
                    decoded_results = results.decode("utf-8")
                except UnicodeDecodeError:
                    decoded_results = ""
            except LookupError:
                try:
                    decoded_results = codecs.decode(results)
                except UnicodeDecodeError:
                    decoded_results = ""
            return self.process_diff(decoded_results)
        else:
            return ([], [], [])

    def untracked(self):
        return self.handle_files([])

    def ignored(self):
        return self.handle_files(['-i'])

    def handle_files(self, additionnal_args):
        if self.on_disk() and self.git_path:
            args = [
                self.git_binary_path,
                '--git-dir=' + self.git_dir,
                '--work-tree=' + self.git_tree,
                'ls-files', '--other', '--exclude-standard',
            ] + additionnal_args + [
                os.path.join(self.git_tree, self.git_path),
            ]
            args = list(filter(None, args))  # Remove empty args
            results = self.run_command(args)
            encoding = self._get_view_encoding()
            try:
                decoded_results = results.decode(encoding.replace(' ', ''))
            except UnicodeError:
                decoded_results = results.decode("utf-8")
            return (decoded_results != "")
        else:
            return False

    def git_commits(self):
        args = [
            self.git_binary_path,
            '--git-dir=' + self.git_dir,
            '--work-tree=' + self.git_tree,
            'log', '--all',
            '--pretty=%s\a%h %an <%aE>\a%ad (%ar)',
            '--date=local', '--max-count=9000'
        ]
        results = self.run_command(args)
        return results

    def git_branches(self):
        args = [
            self.git_binary_path,
            '--git-dir=' + self.git_dir,
            '--work-tree=' + self.git_tree,
            'for-each-ref',
            '--sort=-committerdate',
            '--format=%(subject)\a%(refname)\a%(objectname)',
            'refs/heads/'
        ]
        results = self.run_command(args)
        return results

    def git_tags(self):
        args = [
            self.git_binary_path,
            '--git-dir=' + self.git_dir,
            '--work-tree=' + self.git_tree,
            'show-ref',
            '--tags',
            '--abbrev=7'
        ]
        results = self.run_command(args)
        return results

    def git_current_branch(self):
        args = [
            self.git_binary_path,
            '--git-dir=' + self.git_dir,
            '--work-tree=' + self.git_tree,
            'rev-parse',
            '--abbrev-ref',
            'HEAD'
        ]
        result = self.run_command(args)
        return result

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

        # Ignore White Space Setting
        self.ignore_whitespace = self.settings.get('ignore_whitespace')
        if self.ignore_whitespace == 'all':
            self.ignore_whitespace = '-w'
        elif self.ignore_whitespace == 'eol':
            self.ignore_whitespace = '--ignore-space-at-eol'
        else:
            self.ignore_whitespace = ''

        # Patience Setting
        self.patience_switch = ''
        patience = self.settings.get('patience')
        if patience:
            self.patience_switch = '--patience'

        # Untracked files
        self.show_untracked = self.settings.get(
            'show_markers_on_untracked_file')

        # Show in minimap
        self.show_in_minimap = self.user_settings.get('show_in_minimap') or self.settings.get('show_in_minimap')

        # Show information in status bar
        self.show_status = self.user_settings.get('show_status') or self.settings.get('show_status')
        if self.show_status != 'all' and self.show_status != 'none':
            self.show_status = 'default'
