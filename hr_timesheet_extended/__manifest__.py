{
    'name': 'Extended HR Timesheet with Approval Workflow',
    'version': '17.0.1.0.0',
    'category': 'Human Resources/Timesheets',
    'summary': 'Enhanced timesheet with hierarchical approval workflow and payroll integration',
    'description': """
Extended HR Timesheet with Approval Workflow
============================================

This module extends the standard HR Timesheet functionality with:
- Hierarchical approval workflow
- Automatic activity creation for managers
- Total hours calculation and display
- Minimum work hours and overtime tracking
- Custom Grid view with approval status and totals
- Wizard for creating timesheet approval requests
- Payroll integration with work entry types and payslips
    """,
    'author': 'Odoo Expert',
    'website': 'https://www.example.com',
    'depends': [
        'hr_timesheet',
        'hr',
        'mail',
        'project',
        'timesheet_grid',
        'web',
        'hr_work_entry',
        'hr_payroll',
        'project_timesheet_holidays',
        'calendar',
    ],
    'data': [
        'security/hr_timesheet_security.xml',
        'security/ir.model.access.csv',
        'wizards/hr_timesheet_rejection_wizard_views.xml',
        'wizards/hr_timesheet_to_payroll_wizard_views.xml',
        'data/hr_timesheet_data.xml',
        'views/hr_timesheet_grid_views.xml',
        'views/hr_timesheet_views.xml',
        'views/hr_timesheet_approval_views.xml',
        'views/calendar_event_views.xml',
        'report/timesheet_approval_report_templates.xml'

    ],

    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
