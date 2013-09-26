
import os
import hashlib
from text_profile_signature import tps

def test_tps():
    path = os.path.dirname(__file__)
    path = os.path.join( path, '../../../data/test/' )
    text1 = open(os.path.join(path, 'nytimes-index-clean-visible.html')).read().decode('utf8')
    print hashlib.md5(repr(text1)).hexdigest(), tps(text1)
    text2 = open(os.path.join(path, 'nytimes-index-clean-visible-dup.html')).read().decode('utf8')
    print hashlib.md5(repr(text2)).hexdigest(), tps(text2)