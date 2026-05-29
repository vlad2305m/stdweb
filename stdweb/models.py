from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils.timezone import now
from django.contrib.auth.models import User
from django.conf import settings

import os, shutil
import datetime


def config_get(config, key):
    if config is not None and key in config:
        return config[key]
    # Defaults are kept as a separate object to not pollute the database - is this a good thing?
    _default_config = {
        # (TaskInspectForm) Time string for processing (ISO or similar)
        "time": None,

        # (UploadFileForm / TaskPhotometryForm) Targets to measure; list of {ra,dec}
        "targets": None,
        "target_ra": None,
        "target_dec": None,

        # (TaskPhotometryForm) Initial aperture (pixels)
        "initial_aper": 3.0,
        # (TaskPhotometryForm) Initial smoothing kernel (pixels)
        "initial_r0": 0.0,
        # (TaskPhotometryForm / subtraction) Background mesh size (pixels, width and height)
        "bg_size": 256,
        # (TaskInspectForm / TaskPhotometryForm) Saturation level (ADU)
        "saturation": None,
        # (TaskPhotometryForm) Minimal object area for detection (pixels)
        "minarea": 3,

        # (Photometry/Subtraction) Relative aperture for forced photometry (FWHM units)
        "rel_aper": 1.0,
        "rel_bg1": 5,
        "rel_bg2": 7,

        # (TaskPhotometryForm) Optional override of measured FWHM (pixels)
        "fwhm_override": None,
        # (set by photometry) Measured FWHM (pixels)
        "fwhm": 1.0, # 3 or 1?

        # (TaskPhotometryForm) Filter name used for calibration
        "filter": "",
        # (TaskPhotometryForm) Reference catalogue name
        "cat_name": "",
        "cat_limit": None,
        "cat_col_mag": None,
        "cat_col_mag_err": None,
        "cat_col_color_mag1": None,
        "cat_col_color_mag2": None,

        # (TaskPhotometryForm) Zeropoint spatial order and color usage
        "spatial_order": 0,
        "use_color": False, # True ?
        # (TaskPhotometryForm) Force a color-term fit during calibration
        "force_color_term": False,
        # (TaskPhotometryForm) Background polynomial order for calibration
        "bg_order": None,

        # (TaskPhotometryForm) Matching radius override (arcsec)
        "sr_override": None,

        # (TaskPhotometryForm) Pre-filter detections using artefact classifier
        "prefilter_detections": True,
        # (TaskPhotometryForm) Remove blended catalogue stars
        "filter_blends": True,
        # (TaskPhotometryForm) Diagnostic flag for color term
        "diagnose_color": False,

        # (TaskPhotometryForm) Astrometry refinement flags and options
        "refine_wcs": False, # True?
        "refine_order": 3,
        "blind_match_wcs": False,
        "inspect_bg": False,
        "centroid_targets": False,
        "optimal_extraction": False,
        "nonlin": False,

        # (TaskPhotometryForm) Blind-match scale and center parameters
        "blind_match_ps_lo": 0.2,
        "blind_match_ps_up": 4.0,
        "blind_match_center": None,
        "blind_match_sr0": 2,

        # (Inspect) default S/N threshold used in various plots and checks
        "sn": 5, # was 3 in plotdetectionlimit

        # (Inspect) image gain; usually inferred from FITS header if unset
        "gain": 1.0,
        # (Celery tasks) enable timing markers in log files
        "timing": None,

        # (TaskInspectForm / UploadFileForm) Control running stages
        "run_subtraction": False,
        "run_photometry": False,

        # (TaskTransientsSimpleForm) Simple-transients options
        "simple_skybot": True,
        "simple_others": None,
        "simple_center": None,
        "simple_sr0": None,
        "simple_blends": True,
        "simple_prefilter": True,
        "simple_mag_diff": 2.0,

        # (transients) Cutout size in pixels used when extracting candidates
        "cutout_size": 30,

        # (Views / UploadFileForm) Stack input filenames when stacking multiple images
        "stack_filenames": None,

        # (Views) Free-form target string (one per line) commonly set from forms
        "target": None,

        # (Processing) Pixel scale updated after WCS is available
        "pixscale": None,

        # (Processing / views_skyportal) Photometry-derived magnitude limit
        "mag_limit": None,

        # (SubtractionForm) Template selection and custom template parameters
        "template": "ps1",
        "custom_template_gain": 10000,
        "custom_template_saturation": None,
        "template_fwhm_override": None,

        # (SubtractionForm) Subtraction control
        "sub_size": 1000,
        "sub_overlap": 50,
        "sub_verbose": False,
        "subtraction_method": "hotpants",
        "subtraction_mode": "detection",

        # (SubtractionForm) HOTPANTS / SFFT parameters
        "hotpants_extra": {"ko": 0, "bgo": 0},
        "sfft_kernel_poly_order": 0,
        "sfft_bg_poly_order": 0,
        "sfft_flux_poly_order": 0,

        # (SubtractionForm) Filters for transient search within subtraction
        "filter_vizier": False,
        "filter_skybot": False,
        "filter_prefilter": True,
        "filter_adjust": True,
        "filter_center": None,
        "filter_sr0": 1,

        # (Stacking / UploadFileForm) Stacking behavior
        "stack_method": "sum",
        "stack_subtract_bg": True,
        "stack_mask_cosmics": False,

        # (Inspect/Upload) Mask cosmics while inspecting/stacking
        "mask_cosmics": True,

        # Accessed in crispy fields template apparently because this is initial for all forms
        "form_type": None,
    }

    return _default_config[key]

class Task(models.Model):
    # path = models.CharField(max_length=250, blank=False, unique=True, editable=False) # Base dir where task processing will be performed
    original_name = models.CharField(max_length=250, blank=False) # Original filename
    title = models.CharField(max_length=250, blank=True) # Optional title or comment

    state = models.CharField(max_length=50, blank=False, default='initial') # State of the task

    celery_id = models.CharField(max_length=50, blank=True, null=True, default=None, editable=False) # Celery task ID, when running
    celery_chain_ids = models.JSONField(default=list, blank=True) # List of all subtask IDs in chain
    celery_pid = models.IntegerField(blank=True, null=True, default=None, editable=False) # PID of the Celery worker process

    user =  models.ForeignKey(User, on_delete=models.CASCADE)

    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True) # Updated on every .save()
    completed = models.DateTimeField(default=now, editable=False) # Manually updated on finishing the processing

    # For positional searches
    ra = models.FloatField(blank=True, null=True)
    dec = models.FloatField(blank=True, null=True)
    radius = models.FloatField(blank=True, null=True)
    moc = models.TextField(blank=True, null=True)

    config = models.JSONField(default=dict, blank=True)

    def path(self):
        return os.path.join(settings.TASKS_PATH, str(self.id))

    def complete(self):
        self.completed = now()

    def __str__(self):
        return f"{self.id}: {self.user.username} : {self.original_name}"

    class Meta:
        permissions = [
            ('skyportal_upload', 'Can upload the task results to SkyPortal'),
            ('view_all_tasks', 'Can view tasks from all users'),
            ('edit_all_tasks', 'Can modify tasks from all users'),
        ]


@receiver(pre_delete, sender=Task)
def delete_task_hook(sender, instance, using, **kwargs):
    path = instance.path()

    # Cleanup the data on filesystem related to this model
    if os.path.exists(path):
        shutil.rmtree(path)


class Preset(models.Model):
    name = models.CharField(max_length=250, blank=False) # Preset name
    config = models.JSONField(default=dict, blank=True, help_text='Initial config for the task, in JSON format')
    files = models.TextField(blank=True, help_text='Files to be copied into new task, one per line') # Files to be copied into new task, one per line

    def __str__(self):
        return f"{self.id}: {self.name}"


class ActionLog(models.Model):
    """Model for logging user actions on tasks."""

    ACTION_TYPES = [
        ('task_create', 'Task Created'),
        ('task_delete', 'Task Deleted'),
        ('task_duplicate', 'Task Duplicated'),
        ('task_update', 'Task Updated'),
        ('task_archive', 'Task Archived'),
        ('task_cleanup', 'Task Cleanup'),
        ('processing_start', 'Processing Started'),
        ('processing_complete', 'Processing Completed'),
        ('processing_failed', 'Processing Failed'),
        ('processing_cancel', 'Processing Cancelled'),
        ('file_upload', 'File Uploaded'),
        ('config_change', 'Config Changed'),
    ]

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=50, choices=ACTION_TYPES, db_index=True)
    task = models.ForeignKey(Task, on_delete=models.SET_NULL, null=True, blank=True)
    task_id_ref = models.IntegerField(null=True, blank=True, help_text='Preserved task ID after task deletion')
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['action', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M:%S} - {self.user} - {self.get_action_display()}"
