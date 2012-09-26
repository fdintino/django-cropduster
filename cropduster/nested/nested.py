from django.contrib.admin import helpers

from .options import ModelAdmin


class NestedAdmin(ModelAdmin):
    def get_formset_instances(self, request, instance, is_new=False):
        obj = None
        if not is_new:
            obj = instance

        formset_kwargs = {}
        if request.method == 'POST':
            formset_kwargs.update({
                'data': request.POST,
                'files': request.FILES,
            })
            if is_new:
                formset_kwargs.update({
                    'save_as_new': request.POST.has_key('_saveasnew')
                })

        formset_iterator = super(NestedAdmin, self).get_formset_instances(request, instance, is_new)
        inline_iterator = self.get_inline_instances(request, obj)
        try:
            while True:
                formset = formset_iterator.next()
                inline = inline_iterator.next()
                yield formset
                if inline.inlines and request.method == 'POST':
                    for form in formset.forms:
                        for nested in inline.get_inline_instances(request):
                            InlineFormSet = nested.get_formset(request, form.instance, nested=True)
                            prefix = '%s-%s' % (form.prefix, InlineFormSet.get_default_prefix())
                            nested_formset = InlineFormSet(instance=form.instance, prefix=prefix,
                                **formset_kwargs)
                            # We set `is_nested` to True so that we have a way
                            # to identify this formset as such and skip it if
                            # there is an error in the POST and we have to create
                            # inline admin formsets.
                            nested_formset.is_nested = True
                            yield nested_formset            
        except StopIteration:
            raise

    def get_nested_inlines(self, request, prefix, inline, obj=None):
        nested_inline_formsets = []
        for nested in inline.get_inline_instances(request):
            InlineFormSet = nested.get_formset(request, obj, nested=True)
            nested_prefix = '%s-%s' % (prefix, InlineFormSet.get_default_prefix())
            nested_formset = InlineFormSet(instance=obj, prefix=nested_prefix)
            nested_inline = self.get_nested_inline_admin_formset(request, nested,
                nested_formset, obj)
            nested_inline_formsets.append(nested_inline)
        return nested_inline_formsets

    def get_nested_inline_admin_formset(self, request, inline, formset, obj=None):
        return helpers.InlineAdminFormSet(inline, formset,
            inline.get_fieldsets(request, obj))

    def get_inline_admin_formsets(self, request, formsets, obj=None):
        inline_iterator = self.get_inline_instances(request, obj)
        # The only reason a nested inline admin formset would show up
        # here is if there was an error in the POST.
        # inline_admin_formsets are for display, not data submission,
        # and the way the nested forms are displayed is by setting the
        # 'inlines' attribute on inline_admin_formset.formset.forms items.
        # So we iterate through to find any `is_nested` formsets and save
        # them in dict `orig_nested_formsets`, keyed on the formset prefix,
        # as we'll need to swap out the nested formsets in the
        # InlineAdminFormSet.inlines if we want error messages to appear.
        orig_nested_formsets = {}
        non_nested_formsets = []
        for formset in formsets:
            if getattr(formset, 'is_nested', False):
                orig_nested_formsets[formset.prefix] = formset
            else:
                non_nested_formsets.append(formset)
        super_iterator = super(NestedAdmin, self).get_inline_admin_formsets(request,
            non_nested_formsets, obj)
        formset_iterator = iter(non_nested_formsets)
        try:
            while True:
                formset = formset_iterator.next()
                inline = inline_iterator.next()
                inline_admin_formset = super_iterator.next()

                for form in inline_admin_formset.formset.forms:
                    if form.instance.pk:
                        instance = form.instance
                    else:
                        instance = None
                    form_inlines = self.get_nested_inlines(request, form.prefix, inline, obj=instance)
                    # Check whether nested inline formsets were already submitted.
                    # If so, use the submitted formset instead of the freshly generated
                    # one since it will contain error information and non-saved data
                    # changes.
                    nested_inline_cls_iterator = inline.get_inline_instances(request)
                    for i, form_inline in enumerate(form_inlines):
                        nested_inline_cls = nested_inline_cls_iterator.next()
                        if form_inline.formset.prefix in orig_nested_formsets:
                            orig_nested_formset = orig_nested_formsets[form_inline.formset.prefix]
                            form_inlines[i] = self.get_nested_inline_admin_formset(request,
                                inline=nested_inline_cls,
                                formset=orig_nested_formset,
                                obj=form_inline.formset.instance)
                    form.inlines = form_inlines
                # The empty prefix is used by django javascript when it tries
                # to determine the ids to give to the fields of newly created
                # instances in the form.
                empty_prefix = formset.add_prefix('empty')
                inline_admin_formset.inlines = self.get_nested_inlines(request, empty_prefix, inline)
                yield inline_admin_formset
        except StopIteration:
            raise
