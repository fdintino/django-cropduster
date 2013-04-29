import os
import re

from django.conf import settings

from django import forms
from django.contrib.admin import helpers
from django.contrib.admin.sites import site
from django.core import validators
from django.db.models.fields import related
from django.db.models.fields.files import ImageFieldFile
from django.forms.forms import BoundField
from django.forms.models import ModelMultipleChoiceField
from django.forms.widgets import Input
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.template.loader import render_to_string

from cropduster.generic import GenericInlineFormSet
from cropduster.models import Image, Thumb
from cropduster.utils import get_aspect_ratios, validate_sizes, OrderedDict, get_min_size, relpath

from jsonutil import jsonutil


class CropDusterWidget(Input):

    sizes = None
    auto_sizes = None
    default_thumb = None
    field = None

    def __init__(self, field=None, sizes=None, auto_sizes=None, default_thumb=None, attrs=None):
        self.field = field
        self.sizes = sizes or self.sizes
        self.auto_sizes = auto_sizes or self.auto_sizes
        self.default_thumb = default_thumb or self.default_thumb

        if attrs is not None:
            self.attrs = attrs.copy()
        else:
            self.attrs = {}
    
    def get_media(self):
        """
        A method used to dynamically generate the media property,
        since we may not have the urls ready at the time of import,
        and then the reverse() call would fail.
        """
        from django.forms.widgets import Media as _Media
        media_url = reverse('cropduster-static', kwargs={'path':''})
        media_cls = type('Media', (_Media,), {
            'css': {
                'all': (os.path.join(media_url, 'css/CropDuster.css?v=1'), )
            },
            'js': (os.path.join(media_url, 'js/CropDuster.js'), )
        })
        return _Media(media_cls)
    
    media = property(get_media)
    
    def render(self, name, value, attrs=None, bound_field=None):
        from .admin import cropduster_inline_factory

        final_attrs = self.build_attrs(attrs, type=self.input_type, name=name)

        if name in self.field.seen_field_names:
            return ''
        else:
            self.field.seen_field_names.add(name)

        obj = None
        image_value = ''

        if isinstance(value, ImageFieldFile):
            obj = value.cropduster_image
            image_value = value.name
            value = getattr(obj, 'pk', None)
        else:
            obj = value
            try:
                value = value.pk
            except AttributeError:
                pass

        self.value = value
        thumbs = OrderedDict({})

        if value is None or value == "":
            final_attrs['value'] = ""
        else:
            final_attrs['value'] = value
            if obj is None:
                obj = Image.objects.get(pk=value)
            for thumb in obj.thumbs.filter(name=self.default_thumb).order_by('-width'):
                size_name = thumb.name
                thumbs[size_name] = obj.get_image_url(size_name)

        final_attrs['sizes'] = jsonutil.dumps(self.sizes)
        final_attrs['auto_sizes'] = jsonutil.dumps(self.auto_sizes)

        aspect_ratios = get_aspect_ratios(self.sizes)
        aspect_ratio = jsonutil.dumps(aspect_ratios[0])
        min_size = jsonutil.dumps(get_min_size(self.sizes, self.auto_sizes))

        factory_kwargs = {
            'sizes': self.sizes,
            'auto_sizes': self.auto_sizes,
            'default_thumb': self.default_thumb,
            'model_cls': Image,
        }

        formset_cls = cropduster_formset_factory(instance=obj, **factory_kwargs)
        formset_cls.default_prefix = name

        request = getattr(self, 'request', None)
        request_post = getattr(request, 'POST', None) or None

        formset_kwargs = {
            'data': request_post or bound_field.form.data or None,
            'prefix': name,
        }

        if obj:
            formset_kwargs['instance'] = obj.content_object
            formset_kwargs['queryset'] = obj.__class__._default_manager

        formset = formset_cls(**formset_kwargs)
        inline_cls = cropduster_inline_factory(formset=formset, **factory_kwargs)
        inline_cls.default_prefix = name
        inline = inline_cls(self.parent_model, site)

        parent_admin = getattr(self, 'parent_admin', None)
        root_admin = getattr(parent_admin, 'root_admin', parent_admin)

        if parent_admin and '__prefix__' not in name:
            inline.parent_admin = parent_admin
            existing_prefixes = [getattr(i, 'default_prefix', None) for i in root_admin.inline_instances]
            last_match = -1
            try:
                i = 0
                while True:
                    last_match = existing_prefixes.index(name, last_match + 1)
                    root_admin.inline_instances.pop(last_match - i)
                    i += 1
            except ValueError:
                pass
            root_admin.inline_instances.append(inline)

        fieldsets = list(inline.get_fieldsets(request, obj))
        readonly = list(inline.get_readonly_fields(request, obj))
        inline_admin_formset = helpers.InlineAdminFormSet(inline, formset,
            fieldsets, readonly_fields=readonly, model_admin=root_admin)

        relative_path = relpath(settings.MEDIA_ROOT, settings.CROPDUSTER_UPLOAD_PATH)
        if re.match(r'\.\.', relative_path):
            raise Exception("Upload path is outside of static root")

        static_url = settings.MEDIA_URL + '/' + relative_path + '/'
        static_url = re.sub(r'/+', r'/', static_url)

        return render_to_string("cropduster/custom_field.html", {
            'obj': obj,
            'image_value': image_value,
            'formset': formset,
            'inline_admin_formset': inline_admin_formset,
            'prefix': name,
            'static_url': static_url,
            'min_size': min_size,
            'aspect_ratio': aspect_ratio,
            'default_thumb': self.default_thumb or '',
            'final_attrs': final_attrs,
            'thumbs': thumbs
        })


class CropDusterFormField(forms.Field):

    sizes = None
    auto_sizes = None
    default_thumb = None

    widget = CropDusterWidget

    def __init__(self, sizes=None, auto_sizes=None, default_thumb=None, *args, **kwargs):
        # Used to keep track of field names rendered by the widget, so as not
        # render them twice
        self.seen_field_names = set([])

        if not sizes and self.sizes:
            sizes = self.sizes
        if not auto_sizes and self.auto_sizes:
            auto_sizes = self.auto_sizes
        if not default_thumb and self.default_thumb:
            default_thumb = self.default_thumb

        if default_thumb is None:
            raise ValueError("default_thumb attribute must be defined.")
        
        default_thumb_key_exists = False
        
        try:
            self._sizes_validate(sizes)
            if default_thumb in sizes.keys():
                default_thumb_key_exists = True
        except ValueError as e:
            # Maybe the sizes is none and the auto_sizes is valid, let's
            # try that
            try:
                self._sizes_validate(auto_sizes, is_auto=True)
            except:
                # raise the original exception
                raise e
        
        if auto_sizes is not None:
            self._sizes_validate(auto_sizes, is_auto=True)
            if default_thumb in auto_sizes.keys():
                default_thumb_key_exists = True
        
        if not default_thumb_key_exists:
            raise ValueError("default_thumb attribute does not exist in either sizes or auto_sizes dict.")
        
        self.sizes = sizes
        self.auto_sizes = auto_sizes
        self.default_thumb = default_thumb

        widget = self.widget(field=self, sizes=sizes, auto_sizes=auto_sizes, default_thumb=default_thumb)
        obj = getattr(self, 'obj', None)
        related = getattr(obj, 'related', None)
        widget.parent_model = getattr(related, 'model', None)

        kwargs['widget'] = widget

        super(CropDusterFormField, self).__init__(*args, **kwargs)

    def _sizes_validate(self, sizes, is_auto=False):
        validate_sizes(sizes)    
        if not is_auto:
            aspect_ratios = get_aspect_ratios(sizes)
            if len(aspect_ratios) > 1:
                raise ValueError("More than one aspect ratio: %s" % jsonutil.dumps(aspect_ratios))

    def to_python(self, value):
        value = super(CropDusterFormField, self).to_python(value)
        if value in validators.EMPTY_VALUES:
            return None

        if isinstance(value, basestring) and not value.isdigit():
            return value

        try:
            value = int(str(value))
        except (ValueError, TypeError):
            raise ValidationError(self.error_messages['invalid'])
        return value


def cropduster_widget_factory(sizes, auto_sizes, default_thumb, obj=None):
    related = getattr(obj, 'related', None)
    return type('CropDusterWidget', (CropDusterWidget,), {
        'sizes': sizes,
        'auto_sizes': auto_sizes,
        'default_thumb': default_thumb,
        '__module__': CropDusterWidget.__module__,
        'parent_model': getattr(related, 'model', None),
        'rel_field': getattr(related, 'field', None),
    })


def cropduster_formfield_factory(sizes, auto_sizes, default_thumb, widget=None, obj=None):
    if widget is None:
        widget = cropduster_widget_factory(sizes, auto_sizes, default_thumb, obj)
    return type('CropDusterFormField',
        (CropDusterFormField,), {
            'sizes': sizes,
            'auto_sizes': auto_sizes,
            'default_thumb': default_thumb,
            'widget': widget,
            '__module__': CropDusterFormField.__module__,
            'obj': obj,
    })


class CropDusterThumbField(ModelMultipleChoiceField):

    def clean(self, value):
        """
        Override default validation so that it doesn't throw a ValidationError
        if a given value is not in the original queryset.
        """
        required_msg = self.error_messages['required']
        list_msg = self.error_messages['list']
        ret = value
        try:
            ret = super(CropDusterThumbField, self).clean(value)
        except ValidationError, e:
            if required_msg in e.messages or list_msg in e.messages:
                raise e
        return ret


class CropDusterBoundField(BoundField):

    def as_widget(self, widget=None, attrs=None, only_initial=False):
        """
        Renders the field by rendering the passed widget, adding any HTML
        attributes passed as attrs.  If no widget is specified, then the
        field's default widget will be used.
        """
        if not widget:
            widget = self.field.widget

        attrs = attrs or {}
        auto_id = self.auto_id
        if auto_id and 'id' not in attrs and 'id' not in widget.attrs:
            if not only_initial:
                attrs['id'] = auto_id
            else:
                attrs['id'] = self.html_initial_id

        if not only_initial:
            name = self.html_name
        else:
            name = self.html_initial_name

        widget_kwargs = {
            'attrs': attrs,
        }
        if isinstance(widget, CropDusterWidget):
            widget_kwargs['bound_field'] = self

        return widget.render(name, self.value(), **widget_kwargs)


class CropDusterForm(forms.ModelForm):

    bound_field_cls = CropDusterBoundField

    model = Image

    class Meta:
        fields = ('id', 'crop_x', 'crop_y', 'crop_w', 'crop_h',
                   'path', '_extension', 'default_thumb', 'thumbs',)

    @staticmethod
    def formfield_for_dbfield(db_field, **kwargs):
        if isinstance(db_field, related.ManyToManyField) and db_field.column == 'thumbs':
            return db_field.formfield(form_class=CropDusterThumbField)
        else:
            return db_field.formfield()


class AbstractInlineFormSet(GenericInlineFormSet):

    model = Image
    fields = ('crop_x', 'crop_y', 'crop_w', 'crop_h',
               'path', '_extension', 'default_thumb', 'thumbs',)
    extra_fields = None
    exclude = None
    sizes = None
    auto_sizes = None
    default_thumb = None
    exclude = ["content_type", "object_id"]
    max_num = 1
    can_order = False
    can_delete = True
    extra = 1
    label = "Upload"

    child_formsets = None
    parent_formset = None

    template = 'cropduster/blank.html'

    def __init__(self, *args, **kwargs):
        self.child_formsets = []
        self.parent_formset = kwargs.pop('parent_formset', None)

        self.label = kwargs.pop('label', None) or self.label
        self.sizes = kwargs.pop('sizes', None) or self.sizes
        self.default_thumb = kwargs.pop('default_thumb', None) or self.default_thumb
        self.extra = kwargs.pop('extra', None) or self.extra        
        self.extra_fields = kwargs.pop('extra_fields', None) or self.extra_fields
        if hasattr(self.extra_fields, 'iter'):
            for field in self.extra_fields:
                self.fields.append(field)
        if kwargs.get('prefix'):
            kwargs['prefix'] = re.sub(r'image\-\d+$', 'image', kwargs['prefix'])
        super(AbstractInlineFormSet, self).__init__(*args, **kwargs)

    def get_queryset(self):
        qs = super(AbstractInlineFormSet, self).get_queryset()
        if not len(qs) and getattr(self, '_object_dict', None):
            pk_ids = self._object_dict.keys()
            if len(pk_ids):
                qs = self.model._default_manager.filter(pk__in=pk_ids)
                if not qs.ordered:
                    qs = qs.order_by(self.model._meta.pk.name)
                self._queryset = qs
        return qs

    def _pre_construct_form(self, i, **kwargs):
        """
        Limit the queryset of the thumbs for performance reasons (so that it doesn't
        pull in every available thumbnail into the selectbox)
        """
        qs = self.get_queryset()
        image_id = 0
        try:
            image_id = qs[0].id
        except IndexError:
            pass
        
        # Limit the queryset for performance reasons
        queryset = None
        try:
            queryset = Image.objects.get(pk=image_id).thumbs.get_query_set()
            self.form.base_fields['thumbs'].queryset = queryset
        except Image.DoesNotExist:
            if self.data is not None and len(self.data) > 0:
                thumb_ids = [int(id) for id in self.data.getlist(self.rel_name + '-0-thumbs')]
                queryset = Thumb.objects.filter(pk__in=thumb_ids)
            else:
                # Return an empty queryset
                queryset = Thumb.objects.filter(pk=0)

        if queryset is not None:
            thumb_field = self.form.base_fields['thumbs']
            thumb_field.queryset = queryset
            try:
                if hasattr(thumb_field.widget, 'widget'):
                    thumb_field.widget.widget.choices.queryset = queryset
                else:
                    thumb_field.widget.choices.queryset = queryset
            except AttributeError:
                pass

    def _post_construct_form(self, form, i, **kwargs):
        return form

    @classmethod
    def get_default_prefix(cls):
        default_prefix = getattr(cls, 'default_prefix', None)
        if default_prefix:
            return default_prefix
        opts = cls.model._meta
        return '-'.join((opts.app_label, opts.object_name.lower(),
                        cls.ct_field.name, cls.ct_fk_field.name,
        ))


def cropduster_formset_factory(sizes=None, auto_sizes=None, default_thumb=None,
                               model_cls=Image, formfield_callback=None, instance=None):
    ct_field = model_cls._meta.get_field("content_type")
    ct_fk_field = model_cls._meta.get_field("object_id")

    exclude = [ct_field.name, ct_fk_field.name]

    if formfield_callback is None:
        formfield_callback = CropDusterForm.formfield_for_dbfield

    attrs = {
        "model": model_cls,
        "formfield_overrides": {
            Thumb: {
                'form_class': CropDusterThumbField,
            },
        },
        "formfield_callback": formfield_callback,
        "Meta": type('Meta', (object,), {
            "formfield_callback": formfield_callback,
            "fields": AbstractInlineFormSet.fields,
            "exclude": exclude,
            "model": model_cls,
        }),
        '__module__': CropDusterForm.__module__,
    }

    form = type('CropDusterForm', (CropDusterForm,), attrs)
    
    inline_formset_attrs = {
        "formfield_callback": formfield_callback,
        "ct_field": ct_field,
        "ct_fk_field": ct_fk_field,
        "exclude": exclude,
        "form": form,
        "model": model_cls,
        '__module__': AbstractInlineFormSet.__module__,
    }
    if sizes is not None:
        inline_formset_attrs['sizes'] = sizes
    if auto_sizes is not None:
        inline_formset_attrs['auto_sizes'] = auto_sizes
    if default_thumb is not None:
        inline_formset_attrs['default_thumb'] = default_thumb

    return type('BaseInlineFormSet', (AbstractInlineFormSet, ), inline_formset_attrs)
