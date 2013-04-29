import inspect


def patch_model_form():

    from django.forms import ModelForm
    from django.forms.forms import BoundField

    from django.utils.encoding import force_unicode
    from django.utils.html import conditional_escape
    from django.utils.safestring import mark_safe

    def __iter__(old_func, self):
        for name, field in self.fields.items():
            yield self.bound_field_cls(self, field, name)

    def __getitem__(old_func, self, name):
        "Returns a BoundField with the given name."
        try:
            field = self.fields[name]
        except KeyError:
            raise KeyError('Key %r not found in Form' % name)
        return self.bound_field_cls(self, field, name)

    def _html_output(old_func, self, normal_row, error_row, row_ender, help_text_html, errors_on_separate_row):
        "Helper function for outputting HTML. Used by as_table(), as_ul(), as_p()."
        top_errors = self.non_field_errors() # Errors that should be displayed above all fields.
        output, hidden_fields = [], []

        for name, field in self.fields.items():
            html_class_attr = ''
            bf = self.bound_field_cls(self, field, name)
            bf_errors = self.error_class([conditional_escape(error) for error in bf.errors]) # Escape and cache in local variable.
            if bf.is_hidden:
                if bf_errors:
                    top_errors.extend([u'(Hidden field %s) %s' % (name, force_unicode(e)) for e in bf_errors])
                hidden_fields.append(unicode(bf))
            else:
                # Create a 'class="..."' atribute if the row should have any
                # CSS classes applied.
                css_classes = bf.css_classes()
                if css_classes:
                    html_class_attr = ' class="%s"' % css_classes

                if errors_on_separate_row and bf_errors:
                    output.append(error_row % force_unicode(bf_errors))

                if bf.label:
                    label = conditional_escape(force_unicode(bf.label))
                    # Only add the suffix if the label does not end in
                    # punctuation.
                    if self.label_suffix:
                        if label[-1] not in ':?.!':
                            label += self.label_suffix
                    label = bf.label_tag(label) or ''
                else:
                    label = ''

                if field.help_text:
                    help_text = help_text_html % force_unicode(field.help_text)
                else:
                    help_text = u''

                output.append(normal_row % {
                    'errors': force_unicode(bf_errors),
                    'label': force_unicode(label),
                    'field': unicode(bf),
                    'help_text': help_text,
                    'html_class_attr': html_class_attr
                })

        if top_errors:
            output.insert(0, error_row % force_unicode(top_errors))

        if hidden_fields: # Insert any hidden fields in the last row.
            str_hidden = u''.join(hidden_fields)
            if output:
                last_row = output[-1]
                # Chop off the trailing row_ender (e.g. '</td></tr>') and
                # insert the hidden fields.
                if not last_row.endswith(row_ender):
                    # This can happen in the as_p() case (and possibly others
                    # that users write): if there are only top errors, we may
                    # not be able to conscript the last row for our purposes,
                    # so insert a new, empty row.
                    last_row = (normal_row % {'errors': '', 'label': '',
                                              'field': '', 'help_text':'',
                                              'html_class_attr': html_class_attr})
                    output.append(last_row)
                output[-1] = last_row[:-len(row_ender)] + str_hidden + row_ender
            else:
                # If there aren't any rows in the output, just append the
                # hidden fields.
                output.append(str_hidden)
        return mark_safe(u'\n'.join(output))

    ModelForm.bound_field_cls = BoundField
    wrapfunc(ModelForm, '__iter__', __iter__)
    wrapfunc(ModelForm, '__getitem__', __getitem__)
    wrapfunc(ModelForm, '_html_output', _html_output)


def patch_model_admin():

    from django.contrib.admin.options import ModelAdmin, BaseModelAdmin, InlineModelAdmin
    from cropduster.forms import CropDusterBoundField
    from cropduster.models import Image, CropDusterField

    def model_has_cropduster(model):
        has_cropduster = False
        fields = model._meta.get_m2m_with_model()
        for field, m in fields:
            if hasattr(field, 'rel'):
                rel_model = getattr(field.rel, 'to', None)
                # Only check classes, otherwise we'll get a TypeError
                if not isinstance(rel_model, type):
                    continue
                if issubclass(rel_model, Image):
                    has_cropduster = True
                    break
        return has_cropduster

    def __init__(old_init, self, *args, **kwargs):
        if isinstance(self, ModelAdmin):
            model, admin_site = (args + (None, None))[0:2]
            if not model:
                model = kwargs.get('model')
        else:
            model = self.model
        if model_has_cropduster(model):
            self.form = type('CropDuster%s' % self.form.__name__, (self.form,), {
                'bound_field_cls': CropDusterBoundField,
                '__module__': self.form.__module__,
            })
        old_init(self, *args, **kwargs)
        for inline_instance in getattr(self, 'inline_instances', []):
            inline_instance.root_admin = self

    def formfield_for_dbfield(old_func, self, db_field, **kwargs):
        if isinstance(db_field, CropDusterField):
            kwargs['parent_admin'] = self
            return db_field.formfield(**kwargs)
        return old_func(self, db_field, **kwargs)

    wrapfunc(ModelAdmin, '__init__', __init__)
    wrapfunc(InlineModelAdmin, '__init__', __init__)
    wrapfunc(BaseModelAdmin, 'formfield_for_dbfield', formfield_for_dbfield)


def wrapfunc(obj, attr_name, wrapper, avoid_doublewrap=True):
    """
    Patch obj.<attr_name> so that calling it actually calls, instead,
    wrapper(original_callable, *args, **kwargs)
    """
    # Get the callable at obj.<attr_name>
    call = getattr(obj, attr_name)

    # optionally avoid multiple identical wrappings
    if avoid_doublewrap and getattr(call, 'wrapper', None) is wrapper:
        return

    # get underlying function (if any), and anyway def the wrapper closure
    original_callable = getattr(call, 'im_func', call)

    def wrappedfunc(*args, **kwargs):
        return wrapper(original_callable, *args, **kwargs)

    # set attributes, for future unwrapping and to avoid double-wrapping
    wrappedfunc.original = call
    wrappedfunc.wrapper = wrapper

    # rewrap staticmethod and classmethod specifically (iff obj is a class)
    if inspect.isclass(obj):
        if hasattr(call, 'im_self'):
            if call.im_self:
                wrappedfunc = classmethod(wrappedfunc)
        else:
            wrappedfunc = staticmethod(wrappedfunc)

    # finally, install the wrapper closure as requested
    setattr(obj, attr_name, wrappedfunc)


def unwrapfunc(obj, attr_name):
    """
    Undo the effects of wrapfunc(obj, attr_name, wrapper)
    """
    setattr(obj, attr_name, getattr(obj, attr_name).original)
