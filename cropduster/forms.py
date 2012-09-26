import os
import re

from django.conf import settings

from django import forms
from django.core import validators
from django.db import models
from django.db.models.fields import related
from django.db.models.fields.files import ImageFieldFile
from django.forms.models import ModelMultipleChoiceField
from django.forms.widgets import Input
from django.contrib.contenttypes.generic import generic_inlineformset_factory
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
        self.formset = generic_inlineformset_factory(Image)

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
    
    def render(self, name, value, attrs=None):
        from jsonutil import jsonutil as json
        import simplejson

        from .nested import nested_inline_formset_factory

        final_attrs = self.build_attrs(attrs, type=self.input_type, name=name)
        # Whether we are rendering from the generated inline formset
        # or rendering on the actual form.
        is_formset_render = bool('content_type-object_id' in name)

        is_nested = False

        if name in self.field.seen_field_names:
            return ''
        else:
            self.field.seen_field_names.add(name)

        if '__prefix__' in name or '_set-' in name:
            is_nested = True
            CropDusterNestedInlineFormSet = nested_inline_formset_factory(self.formset)
            self.formset = type('CropDusterNestedInlineFormSet',
                (CropDusterNestedInlineFormSet,), {
                    'sizes': self.sizes,
                    'auto_sizes': self.auto_sizes,
                    'default_thumb': self.default_thumb,})

        image = None
        image_value = ''
        if isinstance(value, models.Manager):
            try:
                value = value.all()[0]
            except IndexError:
                value = None
            else:
                value = value.pk
        elif isinstance(value, Image):
            image = value
            value = value.pk
        elif isinstance(value, ImageFieldFile):
            image = value.cropduster_image
            image_value = value.name
            value = getattr(image, 'pk', None)
        elif value != '' and isinstance(value, basestring) and not value.isdigit():
            try:
                image = Image.objects.get_by_relpath(value)
            except Image.DoesNotExist:
                image = None
                image_value = value
                value = None
            else:
                image_value = value
                value = image.pk

        self.value = value
        thumbs = OrderedDict({})

        if value is None or value == "":
            final_attrs['value'] = ""
        else:
            final_attrs['value'] = value
            if image is None:
                image = Image.objects.get(pk=value)
            for thumb in image.thumbs.filter(name=self.default_thumb).order_by('-width'):
                size_name = thumb.name
                thumbs[size_name] = image.get_image_url(size_name)

        final_attrs['sizes'] = simplejson.dumps(self.sizes)
        final_attrs['auto_sizes'] = simplejson.dumps(self.auto_sizes)

        aspect_ratios = get_aspect_ratios(self.sizes)
        aspect_ratio = json.dumps(aspect_ratios[0])
        min_size = json.dumps(get_min_size(self.sizes, self.auto_sizes))
        prefix = getattr(self.formset, 'prefix', self.formset.get_default_prefix())
        relative_path = relpath(settings.MEDIA_ROOT, settings.CROPDUSTER_UPLOAD_PATH)
        if re.match(r'\.\.', relative_path):
            raise Exception("Upload path is outside of static root")
        url_root = settings.MEDIA_URL + '/' + relative_path + '/'

        static_url = simplejson.dumps(settings.MEDIA_URL + '/' + relative_path + '/')
        return render_to_string("cropduster/custom_field.html", {
            'is_nested': is_nested,
            'image_value': image_value,
            'is_formset_render': is_formset_render,
            'formset': self.formset,
            'inline_admin_formset': self.formset,
            'prefix': prefix,
            'relative_path': relative_path,
            'url_root': url_root,
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
        
        widget = CropDusterWidget(field=self, sizes=sizes, auto_sizes=auto_sizes, default_thumb=default_thumb)
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


def cropduster_formfield_factory(sizes, auto_sizes, default_thumb):
    return type('CropDusterFormField',
        (CropDusterFormField,), {
            'sizes': sizes,
            'auto_sizes': auto_sizes,
            'default_thumb': default_thumb})


def cropduster_widget_factory(sizes, auto_sizes, default_thumb):
    return type('CropDusterWidget', (CropDusterWidget,), {
        'sizes': sizes,
        'auto_sizes': auto_sizes,
        'default_thumb': default_thumb,})


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


class CropDusterForm(forms.ModelForm):

    model = Image

    @staticmethod
    def formfield_for_dbfield(db_field, **kwargs):
        if isinstance(db_field, related.ManyToManyField) and db_field.column == 'thumbs':
            return db_field.formfield(form_class=CropDusterThumbField)
        else:
            return db_field.formfield()

class AbstractInlineFormSet(GenericInlineFormSet):

    model = Image
    fields = ('id', 'crop_x', 'crop_y', 'crop_w', 'crop_h',
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
    
    def __init__(self, *args, **kwargs):
        self.label = kwargs.pop('label', None) or self.label
        self.sizes = kwargs.pop('sizes', None) or self.sizes
        self.default_thumb = kwargs.pop('default_thumb', None) or self.default_thumb
        self.extra = kwargs.pop('extra', None) or self.extra        
        self.extra_fields = kwargs.pop('extra_fields', None) or self.extra_fields
        if hasattr(self.extra_fields, 'iter'):
            for field in self.extra_fields:
                self.fields.append(field)
        
        super(AbstractInlineFormSet, self).__init__(*args, **kwargs)
    
    def _pre_construct_form(self, i, **kwargs):
        """
        Limit the queryset of the thumbs for performance reasons (so that it doesn't
        pull in every available thumbnail into the selectbox)
        """
        image_id = 0
        try:
            image_id = self.queryset[0].id
        except:
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
        """
        Override the id field of the form with our CropDusterFormField and
        override the thumbs queryset for performance.
        """
        # Override the id field to use our custom field and widget that displays the
        # thumbnail and the button that pops up the cropduster window
        form.fields['id'] = CropDusterFormField(
            label = self.label,
            sizes = self.sizes,
            auto_sizes = self.auto_sizes,
            default_thumb=self.default_thumb,
            required=False
        )
        return form


def cropduster_formset_factory(sizes=None, auto_sizes=None, default_thumb=None,
                               model_cls=Image, is_nested=False):
    ct_field = model_cls._meta.get_field("content_type")
    ct_fk_field = model_cls._meta.get_field("object_id")
    
    exclude = [ct_field.name, ct_fk_field.name]
    
    attrs = {
        "model": model_cls,
        "formfield_overrides": {
            Thumb: {
                'form_class': CropDusterThumbField,
            },
        },
        "formfield_callback": CropDusterForm.formfield_for_dbfield,
        "Meta": type('Meta', (object,), {
            "formfield_callback": CropDusterForm.formfield_for_dbfield,
            "fields": AbstractInlineFormSet.fields,
            "exclude": exclude,
            "model": model_cls,
        }),
    }

    if is_nested:
        attrs.update({
            "position": forms.IntegerField(required=False, widget=forms.HiddenInput()),
        })

    form = type('CropDusterForm', (CropDusterForm,), attrs)
    
    inline_formset_attrs = {
        "formfield_callback": CropDusterForm.formfield_for_dbfield,
        "ct_field": ct_field,
        "ct_fk_field": ct_fk_field,
        "exclude": exclude,
        "form": form,
    }
    if sizes is not None:
        inline_formset_attrs['sizes'] = sizes
    if auto_sizes is not None:
        inline_formset_attrs['auto_sizes'] = auto_sizes
    if default_thumb is not None:
        inline_formset_attrs['default_thumb'] = default_thumb

    return type('BaseInlineFormSet', (AbstractInlineFormSet, ), inline_formset_attrs)


BaseInlineFormSet = cropduster_formset_factory()
