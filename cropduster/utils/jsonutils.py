import six
from six.moves import filter
import json

from cropduster.resizing import Size


def json_default(obj):
    if six.callable(getattr(obj, '__serialize__', None)):
        dct = obj.__serialize__()
        module = obj.__module__
        if module == '__builtin__':
            module = None
        if isinstance(obj, type):
            name = obj.__name__
        else:
            name = obj.__class__.__name__
        type_name = u'.'.join(filter(None, [module, name]))
        if type_name == 'cropduster.resizing.Size':
            type_name = 'Size'
        dct.update({'__type__': type_name})
        return dct
    raise TypeError("object of type %s is not JSON serializable" % type(obj).__name__)


def object_hook(dct):
    if dct.get('__type__') in ['Size', 'cropduster.resizing.Size']:
        return Size(
            name=dct.get('name'),
            w=dct.get('w'),
            h=dct.get('h'),
            min_w=dct.get('min_w'),
            min_h=dct.get('min_h'),
            max_w=dct.get('max_w'),
            max_h=dct.get('max_h'),
            retina=dct.get('retina'),
            auto=dct.get('auto'))
    return dct


def dumps(obj, *args, **kwargs):
    kwargs.setdefault('default', json_default)
    return json.dumps(obj, *args, **kwargs)


def loads(s, *args, **kwargs):
    kwargs.setdefault('object_hook', object_hook)
    return json.loads(s, *args, **kwargs)
