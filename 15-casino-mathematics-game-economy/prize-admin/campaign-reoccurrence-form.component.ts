// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Prize Admin - Campaign Reoccurrence Form
// Configures recurring campaign schedules with support for:
// - Daily, weekly, monthly repeat periods
// - Specific weekday selection for weekly campaigns
// - Ending conditions: never, after N occurrences, or on a specific date
//
// This is critical for casino promotion economics (Chapter 13):
// recurring campaigns need careful budgeting because each occurrence
// incurs bonus costs. A weekly free spins promotion running indefinitely
// can silently become the operator's largest expense if not capped.

import { Component } from '@angular/core';
import {
  SimpleCreateEditViewFormComponent
} from '../../../shared/components/simple-create-edit-view-form/simple-create-edit-view-form.component';
import { FormState } from '../../../shared/components/simple-create-edit-view-form/form-state.enum';
import {
  CampaignReoccurrence,
  CampaignReoccurrenceAttribute,
  CampaignReoccurrenceAttributeLabels,
  CampaignReoccurrenceFormData,
  RecurrenceEnding,
  REPEAT_PERIOD_OPTIONS,
  RepeatPeriod
} from '../../../shared/models/campaign-reoccurrence.model';
import { CampaignReoccurrenceService } from '../../../shared/services/campaign-reoccurrence.service';
import { DropdownItem } from '../../../interfaces/dropdown-item.interface';
import { CampaignReoccurrenceFormService } from './campaign-reoccurrence-form.service';

@Component({
  selector: 'app-campaign-reoccurrence-form',
  templateUrl: './campaign-reoccurrence-form.component.html',
  styleUrls: ['./campaign-reoccurrence-form.component.scss']
})
export class CampaignReoccurrenceFormComponent
  extends SimpleCreateEditViewFormComponent<CampaignReoccurrence, CampaignReoccurrenceFormData> {

  public campaignReoccurrenceAttribute = CampaignReoccurrenceAttribute;
  public campaignReoccurrenceAttributeLabel = CampaignReoccurrenceAttributeLabels;
  public formStates = FormState;
  public recurrenceEnding = RecurrenceEnding;
  public periodOptions: DropdownItem[] = REPEAT_PERIOD_OPTIONS;
  public period = RepeatPeriod;

  constructor(
    campaignReoccurrenceService: CampaignReoccurrenceService,
    public formService: CampaignReoccurrenceFormService
  ) {
    super(campaignReoccurrenceService);
  }

  protected initForm(): void {
    this.form = this.formService.getControl(this.formState);
    super.initForm();
    this.recurrenceChanged();
  }

  protected getFormData(): CampaignReoccurrenceFormData {
    return this.formService.getFormData(this.form);
  }

  public resetValues(): void {
    this.form.patchValue(this.formService.getFormValues(this.model));
    this.recurrenceChanged();
  }

  // Enable/disable recurrence-dependent fields based on toggle state
  public recurrenceChanged(): void {
    if (this.formService.recurrence) {
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_COUNT)?.enable();
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_PERIOD)?.enable();
      this.form.get(CampaignReoccurrenceAttribute.ENDING)?.enable();
      this.recurrencePeriodChanged();
    } else {
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_COUNT)?.disable();
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_PERIOD)?.disable();
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS)?.disable();
      this.form.get(CampaignReoccurrenceAttribute.ENDING)?.disable();
    }

    // Trigger validation update on all recurrence fields
    [
      CampaignReoccurrenceAttribute.REPEAT_COUNT,
      CampaignReoccurrenceAttribute.REPEAT_PERIOD,
      CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS,
      CampaignReoccurrenceAttribute.ENDING,
      CampaignReoccurrenceAttribute.ENDING_OCCURRENCES,
      CampaignReoccurrenceAttribute.ENDING_DATE
    ].forEach(attr => {
      this.form.get(attr)?.updateValueAndValidity();
    });
  }

  // Weekday selection only available for weekly recurrence
  public recurrencePeriodChanged(): void {
    if (
      this.formService.recurrencePeriod === RepeatPeriod.WEEK &&
      this.formState !== FormState.VIEW
    ) {
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS)?.enable();
    } else {
      this.form.get(CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS)?.disable();
    }
    this.form.get(CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS)
      ?.updateValueAndValidity();
  }
}
