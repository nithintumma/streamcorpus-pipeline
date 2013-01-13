#!/usr/bin/env python
'''
clean_visible maintains byte position of all visible text in an HTML (or
XML) document and removes all of parts that are not visible in a web
browser.  This allows taggers to operate on the visible-only text and
any standoff annotation can refer to the original byte positions.

This software is released under an MIT/X11 open source license.

Copyright 2012 Diffeo, Inc.
'''

import re
import string
import itertools

## regex to identify all XML-like tags, including SCRIPT and STYLE tags
invisible = re.compile(
    ## capture the visible text before the invisible part
    '''(?P<before>(.|\n)*?)''' + \
    ## capture everything between SCRIPT and STYLE tags,
    '''(?P<invisible>(<script(.|\n)*?</script>|<style(.|\n)*?</style>''' + \
    ## and also everything inside of XML-like tags, even if it
    ## contains newlines
    '''|<(.|\n)*?>))''',
    ## ignore case
    re.I)

def re_based_make_clean_visible(html):
    '''
    Takes an HTML-like binary string as input and returns a binary
    string of the same length with all tags replaced by whitespace.
    This also detects script and style tags, and replaces the text
    between them with whitespace.  

    Pre-existing whitespace of any kind (newlines, tabs) is converted
    to single spaces ' ', which has the same byte length (and
    character length).

    Note: this does not change any characters like &rsquo; and &nbsp;,
    so taggers operating on this text must cope with such symbols.
    Converting them to some other character would change their byte
    length, even if equivalent from a character perspective.

    This is regex based, which can occassionally just hang...
    '''
    text = ''
    for m in invisible.finditer(html):
        text += m.group('before')
        text += ' ' * len(m.group('invisible'))

    ## text better be >= original
    assert len(html) >= len(text), '%d !>= %d' % (len(html), len(text))

    ## capture any characters after the last tag... such as newlines
    tail = len(html) - len(text)
    text += html[-tail:]
    
    ## now they must be equal
    assert len(html) == len(text), '%d != %d' % (len(html), len(text))

    return text

def make_clean_visible(html, tag_replacement_char=' '):
    '''
    Takes an HTML-like binary string as input and returns a binary
    string of the same length with all tags replaced by whitespace.

    This does not detect comments, style, script, link.  It also does
    do anything with HTML-escaped characters.  All of these are
    handled by the clean_html pre-cursor step.

    Pre-existing whitespace of any kind (newlines, tabs) is converted
    to single spaces ' ', which has the same byte length (and
    character length).

    This is a simple state machine iterator without regexes
    '''
    class non_tag_chars(object):
        def __init__(self):
            self.in_tag = False

        def __call__(self, ch):
            if self.in_tag:
                if ch == '>':
                    self.in_tag = False
                if ch == '\n':
                    return '\n'
                else:
                    return tag_replacement_char

            elif ch == '<':
                self.in_tag = True
                return tag_replacement_char

            else:
                return ch
    
    return ''.join( itertools.imap(non_tag_chars(), html) )

def clean_visible(config):
    '''
    returns a kba.pipeline "transform" function that attempts to
    generate stream_item.body.clean_visible from body.clean_html
    '''
    ## make a closure around config
    def _make_clean_visible(stream_item):
        if stream_item.body and stream_item.body.clean_html:
            stream_item.body.clean_visible = \
                make_clean_visible(stream_item.body.clean_html)
        return stream_item

    return _make_clean_visible


def make_clean_visible_file(i_chunk, clean_visible_path):
    '''make a temp file of clean_visible text'''
    _clean = open(clean_visible_path, 'wb')
    for idx, si in enumerate(i_chunk):
        if si.stream_id is None:
            print('si.stream_id is None... ignoring')
            continue
        _clean.write('<FILENAME docid="%s">' % si.stream_id)
        if si.body and si.body.clean_visible:
            ## this is already in utf-8
            _clean.write(si.body.clean_visible)
        _clean.write('</FILENAME>\n')
    _clean.close()
    ## replace this with log.info()
    print clean_visible_path

    '''
    ## hack to capture html for inspection
    _html = open(clean_visible_path + '-html', 'wb')
    for idx, si in enumerate(i_chunk):
        _html.write('<FILENAME docid="%s">' % si.stream_id)
        if si.body and si.body.clean_html:
            _html.write(si.body.clean_html)
        _html.write('</FILENAME>\n')
    _html.close()
    ## replace this with log.info()
    print clean_visible_path + '-html'
    '''
## used in 'cleanse' below
whitespace = re.compile('''(\s|\n)+''', re.UNICODE)
strip_punctuation = {ord(c): u' ' for c in string.punctuation}

def cleanse(span):
    '''Convert a unicode string into a lowercase string with no
punctuation and only spaces for whitespace.

:param span: string
    '''
    assert isinstance(span, unicode), \
        'got non-unicode string %r' % span
    ## lowercase, strip punctuation, and shrink all whitespace
    span = span.lower()
    span = span.translate(strip_punctuation)
    span = whitespace.sub(' ', span)
    ## trim any leading or trailing whitespace
    return span.strip()

if __name__ == '__main__':
    ## a few simple tests
    html = open('nytimes-index.html').read()
    vis1 = re_based_make_clean_visible(html)

    clean_html = open('nytimes-index-clean.html').read()
    vis1 = re_based_make_clean_visible(clean_html)
    vis2 = make_clean_visible(clean_html)

    assert vis1 == vis2, vis2

