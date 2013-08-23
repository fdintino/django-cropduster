# FROM THE FUTURE LOL
from __future__ import division

import os
import re
import copy
import shutil

from django import forms
from django.conf import settings
from django.db.models import Q
from django.forms.models import modelformset_factory, BaseModelFormSet
from django.http import HttpResponse
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.views.decorators.csrf import csrf_exempt

import PIL.Image

from .models import Thumb, Image as CropDusterImage
from .utils import (
    json, get_relative_media_url, get_upload_foldername,
    get_image_extension, get_media_url, get_min_size)
from .exceptions import (json_error, CropDusterViewException,
    CropDusterResizeException, full_exc_info)


# For validation
class UploadForm(forms.Form):

    picture = forms.ImageField(required=True)
    sizes = forms.CharField(required=False)
    thumbs = forms.CharField(required=False)

    def clean_sizes(self):
        sizes = self.cleaned_data.get('sizes')
        try:
            return json.loads(sizes)
        except:
            return []

    def clean_thumbs(self):
        thumbs = self.cleaned_data.get('thumbs')
        try:
            return json.loads(thumbs)
        except:
            return {}


class CropForm(forms.Form):

    class Media:
        css = {'all': (
            u"%scropduster/css/cropduster.css?v=3" % settings.STATIC_URL,
            u"%scropduster/css/jquery.jcrop.css?v=3" % settings.STATIC_URL,
            u"%scropduster/css/upload.css?v=3" % settings.STATIC_URL,
        )}
        js = (
            u"%scropduster/js/json2.js" % settings.STATIC_URL,
            u"%scropduster/js/jquery.class.js" % settings.STATIC_URL,
            u"%scropduster/js/jquery.form.js?v=1" % settings.STATIC_URL,
            u"%scropduster/js/jquery.jcrop.js?v=4" % settings.STATIC_URL,
            u"%scropduster/js/upload.js?v=4" % settings.STATIC_URL,
            u"%scropduster/js/cropduster.js?v=3" % settings.STATIC_URL,
        )

    image_id = forms.IntegerField(required=False)
    orig_image = forms.CharField(max_length=512, required=False)
    orig_w = forms.IntegerField(required=False)
    orig_h = forms.IntegerField(required=False)
    sizes = forms.CharField()
    thumbs = forms.CharField(required=False)
    thumb_name = forms.CharField(required=False)

    def clean_sizes(self):
        try:
            json.loads(self.cleaned_data.get('sizes', '[]'))
        except:
            return []

    def clean_thumbs(self):
        try:
            return json.loads(self.cleaned_data.get('thumbs', '{}'))
        except:
            return {}


class ThumbForm(forms.ModelForm):

    id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    thumbs = forms.CharField(required=False)
    size = forms.CharField(required=False)
    changed = forms.BooleanField(required=False)

    class Meta:
        model = Thumb
        fields = (
            'id', 'name', 'width', 'height',
            'crop_x', 'crop_y', 'crop_w', 'crop_h', 'thumbs', 'size', 'changed')

    def clean_size(self):
        try:
            return json.loads(self.cleaned_data.get('size', 'null'))
        except:
            return None

    def clean_thumbs(self):
        try:
            return json.loads(self.cleaned_data.get('thumbs', '{}'))
        except:
            return {}


class ThumbFormSet(BaseModelFormSet):
    """
    If the form submitted empty strings for thumb pks, change to None before
    calling AutoField.get_prep_value() (so that int('') doesn't throw a
    ValueError).
    """

    def _construct_form(self, i, **kwargs):
        if self.is_bound and i < self.initial_form_count():
            mutable = getattr(self.data, '_mutable', False)
            self.data._mutable = True
            pk_key = "%s-%s" % (self.add_prefix(i), self.model._meta.pk.name)
            self.data[pk_key] = self.data.get(pk_key) or None
            self.data._multable = mutable
        return super(ThumbFormSet, self)._construct_form(i, **kwargs)


def get_admin_base_template():
    try:
        import custom_admin
    except ImportError:
        try:
            import admin_mod
        except ImportError:
            return 'admin/base.html'
        else:
            return 'admin_mod/base.html'
    else:
        return 'custom_admin/base.html'


class FakeQuerySet(object):

    def __init__(self, objs, queryset):
        self.objs = objs
        self.queryset = queryset

    def __iter__(self):
        obj_iter = iter(self.objs)
        while True:
            try:
                yield obj_iter.next()
            except StopIteration:
                break

    def __len__(self):
        return len(self.objs)

    @property
    def ordered(self):
        return True

    @property
    def db(self):
        return self.queryset.db

    def __getitem__(self, index):
        return self.objs[index]


@csrf_exempt
def upload(request):
    if request.method == "GET":
        ctx = {
            'is_popup': True,
            'image_element_id': request.GET.get('el_id', ''),
            'orig_image': '',
            'upload_to': request.GET.get('upload_to', ''),
            'parent_template': get_admin_base_template(),
        }
        initial = {
            'thumb_name': request.GET.get('thumb_name', ''),
        }
        thumb_ids = filter(None, request.GET.get('thumbs', '').split(','))
        try:
            thumb_ids = map(int, thumb_ids)
        except TypeError:
            thumb_ids = []

        thumbs = Thumb.objects.none()
        if thumb_ids:
            thumbs = Thumb.objects.filter(pk__in=thumb_ids)
            thumbs_data = dict([(t['name'], t) for t in thumbs.values('id', 'name', 'width', 'height')])
            initial['thumbs'] =  json.dumps(thumbs_data)

        if request.GET.get('id'):
            try:
                image = CropDusterImage.objects.get(pk=request.GET['id'])
            except CropDusterImage.DoesNotExist:
                pass
            else:
                orig_w, orig_h = image.get_image_size()
                image_path = os.path.split(image.image.name)[0]
                initial.update({
                    'image_id': image.pk,
                    'orig_image': u'/'.join([image_path, 'original' + image.extension]),
                    'orig_w': orig_w,
                    'orig_h': orig_h,
                })
                ctx['image'] = image.get_image_url('_preview')

        # If we have a new image that hasn't been saved yet
        if request.GET.get('image'):
            image_path, basename = os.path.split(request.GET['image'])
            root_path = os.path.join(settings.MEDIA_ROOT, image_path)
            ext = os.path.splitext(basename)[1]
            if os.path.exists(os.path.join(root_path, '_preview%s' % ext)):
                preview_url = u'/'.join([settings.MEDIA_URL, image_path, '_preview%s' % ext])
                preview_url = re.sub(r'(?<!:)/+', '/', preview_url) # Remove double '/'
                orig_image = u"%s/original%s" % (image_path, ext)
                try:
                    img = PIL.Image.open(os.path.join(settings.MEDIA_ROOT, orig_image))
                except:
                    pass
                else:
                    orig_w, orig_h = img.size
                    initial.update({
                        'orig_image': orig_image,
                        'orig_w': orig_w,
                        'orig_h': orig_h,
                    })
                    ctx['image'] = preview_url

        initial['sizes'] = request.GET.get('sizes', '[]')
        sizes = json.loads(initial['sizes'])
        thumb_names = dict([(t.name, t) for t in thumbs])
        formset_initial = []
        size_dict = {}
        # Create a fake queryset with the same order as the image `sizes`.
        # This is necessary if we want the formset thumb order to be consistent.
        ordered_thumbs = []
        for size in sizes:
            size_dict[size.name] = size
            if size.name not in thumb_names:
                ordered_thumbs.append(Thumb(name=size.name))
            else:
                ordered_thumbs.append(thumb_names[size.name])
        fake_queryset = FakeQuerySet(ordered_thumbs, thumbs)

        FormSet = modelformset_factory(Thumb, form=ThumbForm, formset=ThumbFormSet, extra=0)
        thumb_formset = FormSet(queryset=fake_queryset, initial=formset_initial, prefix='thumbs')

        for thumb_form in thumb_formset.initial_forms:
            thumb_form.initial['changed'] = False
            pk = thumb_form.initial['id']
            name = thumb_form.initial['name']
            if name in size_dict:
                thumb_form.initial['size'] = json.dumps(size_dict[name])
            # The thumb being cropped and thumbs referencing it
            thumb_group = thumbs.filter(Q(pk=pk) | Q(reference_thumb_id__exact=pk))
            thumb_group_data = dict([(t['name'], t) for t in thumb_group.values('id', 'name', 'width', 'height')])
            thumb_form.initial['thumbs'] = json.dumps(thumb_group_data)

        ctx.update({
            'upload_form': UploadForm(initial={
                'sizes': json.dumps(sizes),
            }),
            'crop_form': CropForm(initial=initial, prefix='crop'),
            'thumb_formset': thumb_formset,
            'image': ctx.pop('image', u"%scropduster/img/blank.gif" % settings.STATIC_URL),
        })
        return render_to_response('cropduster/upload.html', RequestContext(request, ctx))
    else:
        form = UploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return json_error(request, 'upload', action="uploading file",
                    errors=[unicode(form['picture'].errors)])

        img_file = request.FILES['picture']
        extension = os.path.splitext(img_file.name)[1].lower()
        folder_path = get_upload_foldername(img_file.name,
                upload_to=request.GET.get('upload_to', None))

        tmp_file_path = os.path.join(folder_path, '__tmp' + extension)

        with open(tmp_file_path, 'wb+') as f:
            for chunk in img_file.chunks():
                f.write(chunk)
        img = PIL.Image.open(tmp_file_path)

        (w, h) = (orig_w, orig_h) = img.size
        (min_w, min_h) = get_min_size(request.POST['sizes'])

        if (orig_w < min_w or orig_h < min_h):
            return json_error(request, 'upload', action="uploading file", errors=[(
                u"Image must be at least %(min_w)sx%(min_h)s "
                u"(%(min_w)s pixels wide and %(min_h)s pixels high). "
                u"The image you uploaded was %(orig_w)sx%(orig_h)s pixels.") % {
                    "min_w": min_w,
                    "min_h": min_h,
                    "orig_w": orig_w,
                    "orig_h": orig_h
                }])

        if w <= 0:
            raise json_error(request, 'upload', action='uploading file', errors=[
                u"Invalid image: width is %d" % w])
        elif h <= 0:
            raise json_error(request, 'upload', action='uploading file', errors=[
                u"Invalid image: height is %d" % h])

        # File is good, get rid of the tmp file
        orig_file_path = os.path.join(folder_path, 'original' + extension)
        os.rename(tmp_file_path, orig_file_path)

        orig_url = get_relative_media_url(orig_file_path)

        # First pass resize if it's too large
        # (height > 500 px or width > 800 px)
        resize_ratio = min(800.0 / w, 500.0 / h)
        if resize_ratio < 1:
            w = int(round(w * resize_ratio))
            h = int(round(h * resize_ratio))
            preview_img = img.resize((w, h), PIL.Image.ANTIALIAS)
        else:
            preview_img = img

        preview_file_path = os.path.join(folder_path, '_preview' + extension)
        img_save_params = {}
        if preview_img.format == 'JPEG':
            img_save_params['quality'] = 95
        try:
            preview_img.save(preview_file_path, **img_save_params)
        except KeyError:
            # The user uploaded an image with an invalid file extension, we need
            # to rename it with the proper one.
            extension = get_image_extension(img)

            os.rename(orig_file_path, os.path.splitext(orig_file_path)[0] + extension)
            orig_file_path = os.path.splitext(orig_file_path)[0] + extension
            orig_url = get_relative_media_url(orig_file_path)

            preview_file_path = os.path.join(folder_path, '_preview' + extension)
            preview_img.save(preview_file_path, **img_save_params)

        data = {
            'crop': {
                'orig_image': orig_url,
                'orig_w': orig_w,
                'orig_h': orig_h,
            },
            'url': get_media_url(preview_file_path),
            'orig_image': get_relative_media_url(orig_url),
            'orig_w': orig_w,
            'orig_h': orig_h,
            'width': w,
            'height': h,
            'orig_url': orig_url,
        }
        return HttpResponse(json.dumps(data), mimetype='application/json')


@csrf_exempt
def crop(request):
    try:
        if request.method == "GET":
            raise CropDusterViewException("Form submission invalid")
    except CropDusterViewException as e:
        return json_error(request, 'crop',
            action="cropping image", errors=[e], log=True, exc_info=full_exc_info())

    crop_form = CropForm(request.POST, request.FILES, prefix='crop')
    if not crop_form.is_valid():
        return json_error(request, 'crop', action='submitting form', forms=[crop_form],
                log=True, exc_info=full_exc_info())

    crop_data = copy.deepcopy(crop_form.cleaned_data)
    db_image = CropDusterImage(image=crop_data['orig_image'])
    try:
        pil_image = PIL.Image.open(db_image.image.path)
    except IOError:
        pil_image = None

    FormSet = modelformset_factory(Thumb, form=ThumbForm, formset=ThumbFormSet)
    thumb_formset = FormSet(request.POST, request.FILES, prefix='thumbs')

    if not thumb_formset.is_valid():
        return json_error(request, 'crop', action='submitting form', formsets=[thumb_formset],
                log=True, exc_info=full_exc_info())

    cropped_thumbs = thumb_formset.save(commit=False)

    non_model_fields = set(ThumbForm.declared_fields) - set([f.name for f in Thumb._meta.fields])

    # The fields we will pull from when populating the ThumbForm initial data
    json_thumb_fields = ['id', 'name', 'width', 'height']

    thumbs_with_crops = [t for t in cropped_thumbs if t.crop_w and t.crop_h]
    thumbs_data = [f.cleaned_data for f in thumb_formset]

    for i, (thumb, thumb_form) in enumerate(zip(cropped_thumbs, thumb_formset)):
        changed_fields = set(thumb_form.changed_data) - non_model_fields
        thumb_form._changed_data = list(changed_fields)
        if changed_fields & set(['crop_x', 'crop_y', 'crop_w', 'crop_h']):
            thumb.pk = None

            try:
                new_thumbs = db_image.save_size(thumb_form.cleaned_data['size'], thumb, tmp=True)
            except CropDusterResizeException as e:
                return json_error(request, 'crop',
                                  action="saving size", errors=[unicode(e)])

            if not new_thumbs:
                continue
            cropped_thumbs[i] = thumb = new_thumbs.get(thumb.name, thumb)
            thumbs_data[i].update({
                'crop_x': thumb.crop_x,
                'crop_y': thumb.crop_y,
                'crop_w': thumb.crop_w,
                'crop_h': thumb.crop_h,
                'width':  thumb.width,
                'height': thumb.height,
                'id': thumb.id,
                'changed': True,
            })
            for name, new_thumb in new_thumbs.iteritems():
                if new_thumb.reference_thumb_id:
                    continue
                thumb_data = dict([(k, getattr(new_thumb, k)) for k in json_thumb_fields])
                crop_data['thumbs'].update({name: thumb_data})
                thumbs_data[i]['thumbs'].update({name: thumb_data})
        elif thumb.pk and thumb.name and thumb.crop_w and thumb.crop_h:
            thumb_path = db_image.get_image_path(thumb.name, use_temp=False)
            tmp_thumb_path = db_image.get_image_path(thumb.name, use_temp=True)
            if os.path.exists(thumb_path):
                if not thumb_form.cleaned_data.get('changed') or not os.path.exists(tmp_thumb_path):
                    shutil.copy(thumb_path, tmp_thumb_path)
        if not thumb.pk and not thumb.crop_w and not thumb.crop_h:
            if not len(thumbs_with_crops):
                continue
            best_fit = thumb_form.cleaned_data['size'].fit_to_crop(
                    thumbs_with_crops[0], original_image=pil_image)
            if best_fit:
                thumbs_data[i].update({
                    'crop_x': best_fit.box.x1,
                    'crop_y': best_fit.box.y1,
                    'crop_w': best_fit.box.w,
                    'crop_h': best_fit.box.h,
                    'changed': True,
                })

    for thumb_data in thumbs_data:
        if isinstance(thumb_data['id'], Thumb):
            thumb_data['id'] = thumb_data['id'].pk

    return HttpResponse(json.dumps({
        'crop': crop_data,
        'thumbs': thumbs_data,
        'initial': True,
    }), mimetype='application/json')
