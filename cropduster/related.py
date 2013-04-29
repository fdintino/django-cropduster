from django.core.exceptions import ObjectDoesNotExist, ImproperlyConfigured
from django.core.files.base import File
from django.db import connection, models
from django.db import router, DEFAULT_DB_ALIAS
from django.db.models.loading import get_model
from django.db.models.fields.files import FieldFile
from django.db.models.fields.related import RelatedField, Field
from django.contrib.contenttypes.generic import GenericRel
from django.utils.encoding import smart_unicode

from django.contrib.contenttypes.models import ContentType

from .settings import CROPDUSTER_MEDIA_ROOT


class GenericRelation(RelatedField, Field):
    """Provides an accessor to generic related objects (e.g. comments)"""

    image_kwargs = None
    image_field = None

    generic_descriptor = None
    image_descriptor = None

    def __init__(self, to, **kwargs):
        self.image_kwargs = {}

        for arg in ('unique', 'db_index'):
            if arg in kwargs:
                self.image_kwargs[arg] = kwargs.pop(arg)

        self.image_kwargs.update({
            'editable': False,
            'default': '',
            'blank': True,
            'upload_to': kwargs.pop('upload_to', CROPDUSTER_MEDIA_ROOT),
            'storage': kwargs.pop('storage', None),
            'width_field': kwargs.pop('width_field', None),
            'height_field': kwargs.pop('height_field', None),
            'max_length': kwargs.pop('max_length', 100),
        })

        kwargs['rel'] = GenericRel(to,
                            related_name=kwargs.pop('related_name', None),
                            limit_choices_to=kwargs.pop('limit_choices_to', None),
                            symmetrical=kwargs.pop('symmetrical', True))

        # Override content-type/object-id field names on the related class
        self.object_id_field_name = kwargs.pop("object_id_field", "object_id")
        self.content_type_field_name = kwargs.pop("content_type_field", "content_type")

        kwargs.update({
            'blank': True,
            'editable': True,
            'serialize': False,
            'max_length': self.image_kwargs['max_length'],
        })

        Field.__init__(self, **kwargs)
        self.image_kwargs['db_column'] = kwargs.pop('db_column', self.name)

    def __get__(self, instance, obj_type):
        try:
            return super(GenericRelation, self).__get__(instance, obj_type)
        except AttributeError:
            return self

    def get_choices_default(self):
        return Field.get_choices(self, include_blank=False)

    def value_to_string(self, obj):
        qs = getattr(obj, self.name).all()
        return smart_unicode([instance._get_pk_val() for instance in qs])

    def m2m_db_table(self):
        return self.rel.to._meta.db_table

    def m2m_column_name(self):
        return self.object_id_field_name

    def m2m_reverse_name(self):
        return self.rel.to._meta.pk.column

    def m2m_target_field_name(self):
        return self.model._meta.pk.name

    def m2m_reverse_target_field_name(self):
        return self.rel.to._meta.pk.name

    def contribute_to_class(self, cls, name):
        self.generic_rel_name = '%s_generic_rel' % name
        super(GenericRelation, self).contribute_to_class(cls, name)
        self.image_field_name = name

        # Save a reference to which model this class is on for future use
        self.model = cls

        self.image_field = models.ImageField(**self.image_kwargs)
        ### HACK: manually fix creation counter
        self.image_field.creation_counter = self.creation_counter

        # This calls contribute_to_class() for the ImageField
        cls.add_to_class(self.image_field_name, self.image_field)

        # Add the descriptor for the generic relation
        generic_descriptor = CropDusterDescriptor(self, self.image_field)
        # We use self.__dict__ to avoid triggering __get__()
        self.__dict__['generic_descriptor'] = generic_descriptor
        setattr(cls, self.generic_rel_name, generic_descriptor)

        # Add the descriptor for the image field
        image_descriptor = CropDusterDescriptor(self, self.image_field,
            is_image_field=True)
        self.__dict__['image_descriptor'] = image_descriptor
        setattr(cls, self.image_field_name, image_descriptor)

    def contribute_to_related_class(self, cls, related):
        pass

    def set_attributes_from_rel(self):
        pass

    def get_internal_type(self):
        if self.south_executing:
            return "FileField"
        else:
            return "ManyToManyField"

    def db_type(self, connection):
        if self.south_executing:
            return super(GenericRelation, self).db_type(connection=connection)
        else:
            # Since we're simulating a ManyToManyField, in effect, best return
            # the same db_type as well.
            return None

    def extra_filters(self, pieces, pos, negate):
        """
        Return an extra filter to the queryset so that the results are filtered
        on the appropriate content type.
        """
        if negate:
            return []
        ContentType = get_model("contenttypes", "contenttype")
        content_type = ContentType.objects.get_for_model(self.model)
        prefix = "__".join(pieces[:pos + 1])
        return [("%s__%s" % (prefix, self.content_type_field_name),
            content_type)]

    def bulk_related_objects(self, objs, using=DEFAULT_DB_ALIAS):
        """
        Return all objects related to ``objs`` via this ``GenericRelation``.

        """
        return self.rel.to._base_manager.db_manager(using).filter(**{
                "%s__pk" % self.content_type_field_name:
                    ContentType.objects.db_manager(using).get_for_model(self.model).pk,
                "%s__in" % self.object_id_field_name:
                    [obj.pk for obj in objs]
                })


class CropDusterDescriptor(object):

    def __init__(self, field, image_field, is_image_field=False):
        self.field = field
        self.image_field = image_field
        self.is_image_field = is_image_field

    def __get__(self, instance, instance_type=None):
        if instance is None:
            if self.is_image_field:
                return self.image_field
            else:
                return self.field

        cache_name = self.field.get_cache_name()
        image_val = None

        try:
            if self.is_image_field:
                image_val = instance.__dict__[self.image_field.name]
                raise AttributeError("Lookup related field")                
            else:
                return getattr(instance, cache_name)
        except AttributeError:
            # This import is done here to avoid circular import importing this module
            from django.contrib.contenttypes.models import ContentType
            from .models import Image

            # Dynamically create a class that subclasses the related model's
            # default manager.
            rel_model = self.field.rel.to

            if not issubclass(rel_model, Image):
                raise ImproperlyConfigured(
                    (u"CropDusterField `to` kwarg value must be %(cls_name) or "
                     u" a subclass of %(cls_name)") % {
                        'cls_name': 'cropduster.models.Image'})

            superclass = rel_model._default_manager.__class__
            RelatedManager = create_generic_related_manager(superclass)

            qn = connection.ops.quote_name

            manager = RelatedManager(
                model=rel_model,
                instance=instance,
                field=self.field,
                symmetrical=(self.field.rel.symmetrical and instance.__class__ == rel_model),
                join_table=qn(self.field.m2m_db_table()),
                source_col_name=qn(self.field.m2m_column_name()),
                target_col_name=qn(self.field.m2m_reverse_name()),
                content_type=ContentType.objects.db_manager(instance._state.db).get_for_model(instance),
                content_type_field_name=self.field.content_type_field_name,
                object_id_field_name=self.field.object_id_field_name)

            db = manager._db or router.db_for_read(rel_model, instance=instance)
            query = {
                '%s__pk' % manager.content_type_field_name : manager.content_type.id,
                '%s__exact' % manager.object_id_field_name : manager.pk_val,
            }
            qset = superclass.get_query_set(manager).using(db)
            try:
                val = qset.get(**query)
            except rel_model.DoesNotExist:
                if not self.is_image_field:
                    return None
                else:
                    val = None
            else:
                setattr(instance, cache_name, manager)
                if val.path:
                    image_val = val.get_relative_image_path()
                if not self.is_image_field:
                    return manager

        # Sort out what to do with the image_val
        # If this value is a string (instance.file = "path/to/file") or None
        # then we simply wrap it with the appropriate attribute class according
        # to the file field. [This is FieldFile for FileFields and
        # ImageFieldFile for ImageFields; it's also conceivable that user
        # subclasses might also want to subclass the attribute class]. This
        # object understands how to convert a path to a file, and also how to
        # handle None.
        attr_cls = self.image_field.attr_class
        if isinstance(image_val, basestring) or image_val is None:
            attr = attr_cls(instance, self.image_field, image_val)
            attr.cropduster_image = val
            instance.__dict__[self.image_field.name] = attr

        # Other types of files may be assigned as well, but they need to have
        # the FieldFile interface added to the. Thus, we wrap any other type of
        # File inside a FieldFile (well, the field's attr_class, which is
        # usually FieldFile).
        elif isinstance(image_val, File) and not isinstance(image_val, FieldFile):
            file_copy = attr_cls(instance, self.image_field, image_val.name)
            file_copy.file = image_val
            file_copy.cropduster_image = val
            file_copy._committed = False
            instance.__dict__[self.image_field.name] = file_copy

        # Finally, because of the (some would say boneheaded) way pickle works,
        # the underlying FieldFile might not actually itself have an associated
        # file. So we need to reset the details of the FieldFile in those cases.
        elif isinstance(image_val, FieldFile) and not hasattr(image_val, 'field'):
            image_val.instance = instance
            image_val.field = self.image_field
            image_val.storage = self.image_field.storage
            image_val.cropduster_image = val

        return instance.__dict__[self.image_field.name]

    def __set__(self, instance, value):
        from .models import Image

        if instance is None:
            raise AttributeError("Manager must be accessed via instance")
        if self.is_image_field:
            instance.__dict__[self.image_field.name] = value
        else:
            manager = self.__get__(instance)
            manager.clear()
            if value is None:
                return
            if isinstance(value, Image):
                image_val = value.get_relative_image_path()
                setattr(instance, self.field.image_field_name, image_val)
                manager.add(value)
            else:
                for obj in value:
                    image_val = obj.get_relative_image_path()
                    setattr(instance, self.field.image_field_name, image_val)
                    manager.add(obj)


def create_generic_related_manager(superclass):
    """
    Factory function for a manager that subclasses 'superclass' (which is a
    Manager) and adds behavior for generic related objects.
    """

    class GenericRelatedObjectManager(superclass):
        def __init__(self, model=None, core_filters=None, instance=None,
                     field=None, symmetrical=None, join_table=None,
                     source_col_name=None, target_col_name=None,
                     content_type=None, content_type_field_name=None,
                     object_id_field_name=None):

            super(GenericRelatedObjectManager, self).__init__()
            self.core_filters = core_filters or {}
            self.model = model
            self.content_type = content_type
            self.symmetrical = symmetrical
            self.instance = instance
            self._field = field
            self.join_table = join_table
            self.join_table = model._meta.db_table
            self.source_col_name = source_col_name
            self.target_col_name = target_col_name
            self.content_type_field_name = content_type_field_name
            self.object_id_field_name = object_id_field_name
            self.pk_val = self.instance._get_pk_val()
            self.image_field_name = self._field.image_field_name

        def get_query_set(self):
            db = self._db or router.db_for_read(self.model, instance=self.instance)
            query = {
                '%s__pk' % self.content_type_field_name : self.content_type.id,
                '%s__exact' % self.object_id_field_name : self.pk_val,
            }
            return superclass.get_query_set(self).using(db).filter(**query)

        def add(self, *objs):
            for obj in objs:
                if not isinstance(obj, self.model):
                    raise TypeError("'%s' instance expected" % self.model._meta.object_name)
                setattr(obj, self.content_type_field_name, self.content_type)
                setattr(obj, self.object_id_field_name, self.pk_val)
                obj.save()
                related_obj = self.__get_related_obj()
                setattr(related_obj, self.image_field_name, obj.path)
        add.alters_data = True

        @property
        def field(self):
            related_obj = self.__get_related_obj()
            return related_obj._meta.get_field(self.image_field_name)

        def __get_related_obj(self):
            related_cls = self.content_type.model_class()
            related_obj = related_cls.objects.get(pk=self.pk_val)
            return related_obj

        def remove(self, *objs):
            db = router.db_for_write(self.model, instance=self.instance)
            for obj in objs:
                obj.delete(using=db)
            try:
                related_obj = self.__get_related_obj()
            except ObjectDoesNotExist:
                pass
            else:
                setattr(related_obj, self.image_field_name, None)
        remove.alters_data = True

        def clear(self):
            db = router.db_for_write(self.model, instance=self.instance)
            for obj in self.all():
                obj.delete(using=db)
            related_obj = self.__get_related_obj()
            setattr(related_obj, self.image_field_name, None)
        clear.alters_data = True

        def create(self, **kwargs):
            kwargs[self.content_type_field_name] = self.content_type
            kwargs[self.object_id_field_name] = self.pk_val
            db = router.db_for_write(self.model, instance=self.instance)
            super_ = super(GenericRelatedObjectManager, self).using(db)
            new_obj = super_.create(**kwargs)
            if new_obj.path:
                related_obj = self.__get_related_obj()
                setattr(related_obj, self.image_field_name, new_obj.path)
            return new_obj
        create.alters_data = True

    return GenericRelatedObjectManager
