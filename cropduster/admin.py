from django.contrib.contenttypes.generic import GenericInlineModelAdmin
from django.db.models.fields import related
from django.utils.functional import curry

from .models import Image
from .forms import (CropDusterFormField,
                    CropDusterThumbField, cropduster_formset_factory)


class BaseImageInline(GenericInlineModelAdmin):
    sizes = None
    auto_sizes = None
    default_thumb = None

    model = Image
    template = "cropduster/blank.html"
    extra = 1
    max_num = 1
    extra_fields = []

    fieldsets = (
        ('Image', {
            'fields': ('id', 'crop_x', 'crop_y', 'crop_w', 'crop_h',
                       'path', '_extension', 'default_thumb', 'thumbs',),
        }),
    )

    def __init__(self, *args, **kwargs):
        try:
            fields = list(self.fieldsets[0][1]['fields'])
            for field in self.extra_fields:
                fields.append(field)
            self.fieldsets[0][1]['fields'] = tuple(fields)
        except:
            pass

        super(BaseImageInline, self).__init__(*args, **kwargs)

    def formfield_for_dbfield(self, db_field, request=None, **kwargs):
        if isinstance(db_field, related.ManyToManyField) and db_field.column == 'thumbs':
            return db_field.formfield(form_class=CropDusterThumbField)
        else:
            return super(BaseImageInline, self).formfield_for_dbfield(db_field, request=request, **kwargs)

    def get_formset(self, request, obj=None):
        formset = cropduster_formset_factory(sizes=self.sizes, auto_sizes=self.auto_sizes, default_thumb=self.default_thumb,
            model_cls=self.model, formfield_callback=curry(self.formfield_for_dbfield, request=request))
        default_prefix = getattr(self, 'default_prefix', None)
        if default_prefix:
            formset.default_prefix = default_prefix
        return formset


def cropduster_inline_factory(sizes, auto_sizes, default_thumb,
                              model_cls=None, formset=None, template=None):
    attrs = {
        'sizes': sizes,
        'auto_sizes': auto_sizes,
        'default_thumb': default_thumb,
        '__module__': BaseImageInline.__module__
    }
    if model_cls is not None:
        attrs['model'] = model_cls

    if formset is not None:
        attrs['formset'] = formset

    if template is not None:
        attrs['template'] = template

    return type("CropDusterImageInline", (BaseImageInline,), attrs)


# Retained for backwards compatibility, but imports of these classes from this module
# are deprecated
from cropduster.forms import CropDusterFormField as _CropDusterFormField
from cropduster.forms import CropDusterThumbField as _CropDusterThumbField

class CropDusterFormField(_CropDusterFormField):
    def __init__(self, *args, **kwargs):
        import warnings
        warnings.warn(
            'Calls to cropduster.admin.CropDusterFormField are deprecated. Please use ' +\
            'cropduster.forms.CropDusterFormField',
            PendingDeprecationWarning
        )
        return super(CropDusterFormField, self).__init__(*args, **kwargs)


class CropDusterThumbField(_CropDusterThumbField):
    def __init__(self, *args, **kwargs):
        import warnings
        warnings.warn(
            'Calls to cropduster.admin.CropDusterThumbField are deprecated. Please use ' +\
            'cropduster.forms.CropDusterThumbField',
            PendingDeprecationWarning
        )
        return super(CropDusterThumbField, self).__init__(*args, **kwargs)
