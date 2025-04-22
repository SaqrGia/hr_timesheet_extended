{
    'name': 'Extended HR Timesheet with Approval Workflow',
    'version': '1.0',
    'category': 'Human Resources/Timesheets',
    'summary': 'Enhanced timesheet with hierarchical approval workflow',
    'description': """
Extended HR Timesheet with Approval Workflow
============================================

This module extends the standard HR Timesheet functionality with:
- Hierarchical approval workflow
- Automatic activity creation for managers
- Total hours calculation and display
- Minimum work hours and overtime tracking
    """,
    'depends': [
        'hr_timesheet',
        'hr',
        'mail',
        'project',
        'timesheet_grid',
    ],
    'data': [
        'security/hr_timesheet_security.xml',
        'security/ir.model.access.csv',
        'data/hr_timesheet_data.xml',
        'views/hr_timesheet_views.xml',
        'views/hr_timesheet_approval_views.xml',
        'report/timesheet_approval_report_templates.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}