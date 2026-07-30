// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Prize Admin - Campaign Reoccurrence Form Service
// Manages the reactive form configuration for recurring campaigns.
// Demonstrates conditional validation -- recurrence-dependent fields
// only validate when recurrence is enabled, preventing false validation
// errors when the feature is toggled off.
//
// The max occurrences cap (10) is a business rule: unlimited recurring
// campaigns were causing budget overruns because marketing teams
// would forget to stop campaigns after promotion periods ended.

import { Injectable } from '@angular/core';
import { FormControl, FormGroup, ValidatorFn, Validators } from '@angular/forms';
import { FormState } from '../../../shared/components/simple-create-edit-view-form/form-state.enum';
import {
  CampaignReoccurrenceAttribute,
  CampaignReoccurrenceFormData,
  RecurrenceEnding,
  RepeatPeriod
} from '../../../shared/models/campaign-reoccurrence.model';
import { ExtendedFormControl } from '../../../directives/extended-form-control';
import {
  ConditionalValidator,
  DateFormatValidator
} from '../../../shared/constants/validators.constant';
import { WEEKDAYS } from '../../../shared/components/weekday-selector/weekday-selector.constant';
import {
  DATETIME_FORMAT_BACKEND_MOMENT,
  DATETIME_FORMAT_NO_SEC_MOMENT
} from '../../../constants/date-formats.constant';
import * as moment from 'moment';

@Injectable()
export class CampaignReoccurrenceFormService {
  public readonly minOccurrences: number = 1;
  public readonly maxOccurrences: number = 10;

  private form!: FormGroup;

  get recurrence(): boolean {
    return this.form.get(CampaignReoccurrenceAttribute.RECURRENCE)?.value;
  }

  get recurrencePeriod(): RepeatPeriod {
    return this.form.get(CampaignReoccurrenceAttribute.REPEAT_PERIOD)?.value;
  }

  public getControl(formState: FormState): FormGroup {
    this.form = new FormGroup({
      [CampaignReoccurrenceAttribute.ID]:
        new FormControl({ value: '', disabled: true }, Validators.required),

      [CampaignReoccurrenceAttribute.NAME]:
        new FormControl(
          { value: '', disabled: formState === FormState.VIEW },
          Validators.required
        ),

      [CampaignReoccurrenceAttribute.RECURRENCE]:
        new ExtendedFormControl({
          value: '',
          disabled: formState === FormState.VIEW
        }),

      // Repeat count: how many periods between occurrences
      // (e.g., "every 2 weeks" = repeat_count: 2, period: WEEK)
      [CampaignReoccurrenceAttribute.REPEAT_COUNT]:
        new ExtendedFormControl(
          { value: 1, disabled: formState === FormState.VIEW },
          ConditionalValidator(
            () => this.recurrence,
            Validators.required
          )
        ),

      [CampaignReoccurrenceAttribute.REPEAT_PERIOD]:
        new ExtendedFormControl(
          { value: '', disabled: formState === FormState.VIEW },
          ConditionalValidator(
            () => this.recurrence,
            Validators.required
          )
        ),

      // Weekday selection: only for weekly recurrence
      [CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS]:
        new ExtendedFormControl(
          {
            value: Object.keys(WEEKDAYS).map(k => +k),
            disabled: formState === FormState.VIEW
          },
          ConditionalValidator(
            () => this.recurrence && this.recurrencePeriod === RepeatPeriod.WEEK,
            Validators.required
          )
        ),

      [CampaignReoccurrenceAttribute.ENDING]:
        new ExtendedFormControl(
          { value: RecurrenceEnding.NEVER, disabled: formState === FormState.VIEW },
          ConditionalValidator(() => this.recurrence, Validators.required)
        ),

      // Max occurrences capped at 10 to prevent runaway campaign costs
      [CampaignReoccurrenceAttribute.ENDING_OCCURRENCES]:
        new ExtendedFormControl(
          { value: 1, disabled: formState === FormState.VIEW },
          [
            ConditionalValidator(() => this.recurrence, Validators.required) as ValidatorFn,
            ConditionalValidator(() => this.recurrence, Validators.min(this.minOccurrences)) as ValidatorFn,
            ConditionalValidator(() => this.recurrence, Validators.max(this.maxOccurrences)) as ValidatorFn,
          ]
        ),

      [CampaignReoccurrenceAttribute.ENDING_DATE]:
        new ExtendedFormControl(
          { value: '', disabled: formState === FormState.VIEW },
          [
            ConditionalValidator(() => this.recurrence, Validators.required) as ValidatorFn,
            ConditionalValidator(
              () => this.recurrence,
              DateFormatValidator(DATETIME_FORMAT_NO_SEC_MOMENT) as ValidatorFn
            ) as ValidatorFn
          ]
        ),
    });

    return this.form;
  }

  public getFormValues(model: any): Record<string, any> {
    return {
      [CampaignReoccurrenceAttribute.ID]: model[CampaignReoccurrenceAttribute.ID],
      [CampaignReoccurrenceAttribute.NAME]: model[CampaignReoccurrenceAttribute.NAME],
      [CampaignReoccurrenceAttribute.RECURRENCE]: model[CampaignReoccurrenceAttribute.RECURRENCE],
      [CampaignReoccurrenceAttribute.REPEAT_PERIOD]: model[CampaignReoccurrenceAttribute.REPEAT_PERIOD],
      [CampaignReoccurrenceAttribute.REPEAT_COUNT]: model[CampaignReoccurrenceAttribute.REPEAT_COUNT],
      [CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS]: model[CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS],
      [CampaignReoccurrenceAttribute.ENDING]: model[CampaignReoccurrenceAttribute.ENDING],
      [CampaignReoccurrenceAttribute.ENDING_OCCURRENCES]: model[CampaignReoccurrenceAttribute.ENDING_OCCURRENCES],
      [CampaignReoccurrenceAttribute.ENDING_DATE]:
        moment.utc(model[CampaignReoccurrenceAttribute.ENDING_DATE])
          .format(DATETIME_FORMAT_NO_SEC_MOMENT)
    };
  }

  public getFormData(form: FormGroup): CampaignReoccurrenceFormData {
    return {
      [CampaignReoccurrenceAttribute.NAME]:
        form.value[CampaignReoccurrenceAttribute.NAME],
      [CampaignReoccurrenceAttribute.RECURRENCE]:
        !!form.value[CampaignReoccurrenceAttribute.RECURRENCE],
      [CampaignReoccurrenceAttribute.REPEAT_PERIOD]:
        form.value[CampaignReoccurrenceAttribute.REPEAT_PERIOD] || null,
      [CampaignReoccurrenceAttribute.REPEAT_COUNT]:
        form.value[CampaignReoccurrenceAttribute.REPEAT_COUNT]
          ? +form.value[CampaignReoccurrenceAttribute.REPEAT_COUNT]
          : null,
      [CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS]:
        form.value[CampaignReoccurrenceAttribute.REPEAT_WEEKDAYS] || null,
      [CampaignReoccurrenceAttribute.ENDING]:
        form.value[CampaignReoccurrenceAttribute.ENDING] || null,
      [CampaignReoccurrenceAttribute.ENDING_OCCURRENCES]:
        form.value[CampaignReoccurrenceAttribute.ENDING_OCCURRENCES]
          ? +form.value[CampaignReoccurrenceAttribute.ENDING_OCCURRENCES]
          : null,
      [CampaignReoccurrenceAttribute.ENDING_DATE]:
        form.value[CampaignReoccurrenceAttribute.ENDING_DATE]
          ? moment(form.value[CampaignReoccurrenceAttribute.ENDING_DATE])
              .format(DATETIME_FORMAT_BACKEND_MOMENT)
          : null
    };
  }
}
