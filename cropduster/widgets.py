from django.forms import HiddenInput
from coffin.template import Context, loader
from django.core.urlresolvers import reverse
from .models import SizeSet, Image as CropDusterImage
from .settings import CROPDUSTER_MEDIA_URL

class AdminCropdusterWidget(HiddenInput):
	def __init__(self, size_set_slug, template="admin/inline.html", *args, **kwargs):
		try:
			self.size_set = SizeSet.objects.get(slug=size_set_slug)
		except:
			self.size_set = None
		self.template = template
		super(AdminCropdusterWidget, self).__init__(*args, **kwargs)
		self.is_hidden = False
	
	def render(self, name, value, attrs=None):
		attrs.setdefault("class", "cropduster")
		
		cropduster_url = reverse("cropduster-upload")
	
		input = super(HiddenInput, self).render(name, value, attrs)
		
		try:
			image = CropDusterImage.objects.get(id=value)
		except:
			image = None
		
		
		t = loader.get_template(self.template)
		c = Context({
			"image": image,
			"size_set": self.size_set,
			"media_url": CROPDUSTER_MEDIA_URL,
			"cropduster_url": cropduster_url,
			"input": input,
			"attrs": attrs,
		})
		return t.render(c)