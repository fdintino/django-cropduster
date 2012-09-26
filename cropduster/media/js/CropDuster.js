window.CropDuster = {};


(function($) {

	CropDuster = {

		adminMediaPrefix: '',
		staticUrl: '',
		// These are set in inline SCRIPT tags, and accessed in
		// the popup window via window.opener. They are keyed on
		// the input id.
		
		// json string of the 'sizes' attribute passed to the form field
		sizes: {},
		
		// json string of the 'auto_sizes' attribute passed to the form field
		autoSizes: {},
		
		// The default thumb. This is what is displayed in the preview box after upload.
		defaultThumbs: {},
		
		// aspect ratio of the 'sizes' attribute. float.
		aspectRatios: {},
		
		formsetPrefixes: {},
		
		minSize: {},
		
		win: null,
		
		init: function() {
			// Deduce adminMediaPrefix by looking at the <script>s in the
			// current document and finding the URL of *this* module.
			var scripts = document.getElementsByTagName('script');
			for (var i=0; i<scripts.length; i++) {
				if (scripts[i].src.match(/addCropDuster/)) {
					var idx = scripts[i].src.indexOf('cropduster/js/addCropDuster');
					CropDuster.adminMediaPrefix = scripts[i].src.substring(0, idx);
					break;
				}
			}
		},
		
		getVal: function(id, name) {
			prefix = CropDuster.formsetPrefixes[id];
			var val = $('#id_' + prefix + '-0-' + name).val();
			return (val) ? encodeURI(val) : val;
		},
		
		setVal: function(id, name, val) {
			prefix = CropDuster.formsetPrefixes[id];
			$('#id_' + prefix + '-0-' + name).val(val);
		},
		
		// open upload window
		show: function(id, href, imageId) {
			var id2=String(id).replace(/\-/g,"____").split(".").join("___");
			var path = CropDuster.getVal(id, 'path');
			if (imageId) {
				href += '&id=' + imageId;
			}
			if (imageId || path) {
				href += '&x=' + CropDuster.getVal(id, 'crop_x');
				href += '&y=' + CropDuster.getVal(id, 'crop_y');
				href += '&w=' + CropDuster.getVal(id, 'crop_w');
				href += '&h=' + CropDuster.getVal(id, 'crop_h');
				href += '&path=' + CropDuster.getVal(id, 'path');
				href += '&ext='  + CropDuster.getVal(id, '_extension');
			}
			href += '&el_id=' + encodeURI(id);
			var win = window.open(href, id2, 'height=650,width=960,resizable=yes,scrollbars=yes');
			win.focus();
		},
		
		setThumbnails: function(id, thumbs) {
			prefix = CropDuster.formsetPrefixes[id];
			select = $('#id_' + prefix + '-0-thumbs');
			select.find('option').detach();
			for (var sizeName in thumbs) {
				var thumbId = thumbs[sizeName];
				var option = $(document.createElement('OPTION'));
				option.attr('value', thumbId);
				option.html(sizeName + ' (' + thumbId + ')');
				select.append(option);
				option.selected = true;
				option.attr('selected', 'selected');
			}
		},
		
		complete: function(id, data) {
			$('#id_' + id).val(data.relpath);
			CropDuster.setVal(id, 'id', data.id);
			CropDuster.setVal(id, 'crop_x', data.x);
			CropDuster.setVal(id, 'crop_y', data.y);
			CropDuster.setVal(id, 'crop_w', data.w);
			CropDuster.setVal(id, 'crop_h', data.h);
			CropDuster.setVal(id, 'path', data.path);
			CropDuster.setVal(id, 'default_thumb', data.default_thumb);
			CropDuster.setVal(id, '_extension', data.extension);
			prefix = CropDuster.formsetPrefixes[id];
			$('#id_' + prefix + '-TOTAL_FORMS').val('1');
			var thumbs;

			if (data.thumbs) {
				thumbs = $.parseJSON(data.thumbs);
				CropDuster.setThumbnails(id, thumbs);
			}
			var defaultThumbName = CropDuster.defaultThumbs[id];
			if (data.thumb_urls) {
				var thumbUrls = $.parseJSON(data.thumb_urls);
				var html = '';
				var i = 0;
				for (var name in thumbUrls) {
					if (name != defaultThumbName) {
						continue;
					}
					var url = thumbUrls[name];
					var className = "preview";
					if (i === 0) {
						className += " first";
					}
					// Append random get variable so that it refreshes
					url += '?rand=' + CropDuster.generateRandomId();
					html += '<img id="' + id + '_image_' + name + '" src="' + url + '" class="' + className + '" />';
					i++;
				}
				var previewId;
				for (var formsetPrefix in CropDuster.formsetPrefixes) {
					if (formsetPrefix != id) {
						if (CropDuster.formsetPrefixes[formsetPrefix] == prefix) {
							previewId = 'preview_id_' + formsetPrefix;
							break;
						}
					}
				}
				if (previewId) {
					$('#' + previewId).html(html);
				}
			}
		},

		/**
		 * Takes an <input class="cropduster-data-field"/> element
		 */
		registerInput: function(input) {
			var $input = $(input);
			var data = $input.data();
			var name = $input.attr('name');
            var matches = name.match(/^(.*_set-\d+-)/);
            if (matches) {
                data.formsetPrefix = matches[1] + data.formsetPrefix;
            }
			CropDuster.sizes[name] = JSON.stringify(data.sizes);
			CropDuster.autoSizes[name] = JSON.stringify(data.autoSizes);
			CropDuster.minSize[name] = data.minSize;
			CropDuster.defaultThumbs[name] = data.defaultThumb;
			CropDuster.aspectRatios[name] = data.aspectRatio;
			CropDuster.formsetPrefixes[name] = data.formsetPrefix;

            // if (data.formsetPrefix == 'cropduster-image-content_type-object_id') {
            // }

			var $customField = $input.parent().find('> .cropduster-customfield');

			$customField.click(function(e) {
				e.preventDefault();
				var $targetParent = $(e.target).closest('.row');
				var $targetInput = $targetParent.find('input');
				if (!$targetInput.length) {
					return;
				}
				$targetInput = $($targetInput[0]);
				var name = $targetInput.attr('name');
				var imageId = $targetInput.val();
				var formsetPrefix = CropDuster.formsetPrefixes[name];

				if (name.indexOf(formsetPrefix) == -1) {
					var $realInput = $('#id_' + formsetPrefix + '-0-id');
					if ($realInput && $realInput.length) {
						imageId = $realInput.val();
						$targetParent = $realInput.closest('.row');
					}
				}

				var fieldName = $targetParent.find('.cropduster-data-field').attr('name');
				CropDuster.show(fieldName, CropDuster.uploadUrl, imageId);
			});


			var $parentForm = $input.parents('.cropduster-form');
			if ($parentForm.length) {
				$parentForm = $($parentForm[0]);
			}

            var matches = name.match(/(?:\d+|__prefix__|empty)\-([^\-]+)$/);
            if (matches) {
                name = matches[1];
            }

			var $inputRow = $input.parents('.row.' + name);
			if ($inputRow.length) {
				var inputLabel = $inputRow.find('label').html();
				if (inputLabel) {
					inputLabel = inputLabel.replace(/:$/, '');
					$parentForm.find('h2.collapse-handler').each(function(header) {
						header.innerHTML = inputLabel;
					});
				}
                $inputRow.find('.cropduster-text-field').hide();
			}

			$parentForm.find('span.delete input').change(function() {
				form = $(this).parents('.cropduster-form');
				if (this.checked) {
					form.addClass('pre-delete');
				} else {
					form.removeClass('pre-delete');
				}
			});
			// Re-initialize thumbnail images. This is necessary in the event that
			// that the cropduster admin form doesn't have an image id but has thumbnails
			// (for example when a new image is uploaded and the post is saved, but there is
			// a validation error on the page)
			$parentForm.find('.id').each(function(i, el) {

				if ($(el).parents('.inline-related').hasClass('empty-form')) {
					return;
				}

				var idName = $(el).find('input').attr('name');

				var matches = /^(.+)-0-id$/.exec(idName);
				if (!matches || matches.length != 2) {
					return;
				}

				var prefix = matches[1];
				var path = $('#id_' + prefix + '-0-path').val();

				ext = $('#id_' + prefix + '-0-_extension').val();
				var html = '';
				var defaultThumbName = CropDuster.defaultThumbs[prefix+'-0-id'];
				$('#id_' + prefix + '-0-thumbs option').each(function(i, el) {
					var name = $(el).html();
					if (name != defaultThumbName) {
						return;
					}
					var url = CropDuster.staticUrl + '/' + path + '/' + name + '.' + ext;
					// This is in place of a negative lookbehind. It replaces all
					// double slashes that don't follow a colon.
					url = url.replace(/(:)?\/+/g, function($0, $1) { return $1 ? $0 : '/'; });
					url += '?rand=' + CropDuster.generateRandomId();
					var className = 'preview';
					if (i === 0) {
						className += ' first';
					}
					html += '<img id="id_' + prefix + '0--id_image_' + name + '" src="' + url + '" class="' + className + '" />';
				});
				$(el).find('cropduster-preview').html(html);
				// $('#preview_id_' + idName).html(html);
			});

		},

		generateRandomId: function() {
			return ('000000000' + Math.ceil(Math.random()*1000000000).toString()).slice(-9);
		}
	};
	
	CropDuster.init();
	
	$(document).ready(function(){

		$('.cropduster-data-field').each(function(i, idField) {
			CropDuster.registerInput(idField);
		});


	});
	
})((typeof window.django != 'undefined') ? django.jQuery : jQuery);
