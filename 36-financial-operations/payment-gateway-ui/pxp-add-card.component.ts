// Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Card Entry Component
// Implements PCI-compliant card entry form with:
// - Credit card type detection (Visa, Mastercard, Amex, Discover)
// - Input masking for card numbers
// - CVV length adjustment based on card type (3 for Visa/MC, 4 for Amex)
// - Real-time expiry date validation
// - Auto-tab between fields when max length reached
//
// Uses the credit-card-type library for BIN-range detection and
// reactive form validation with Angular's ControlValueAccessor pattern.

import { Component, Input, OnInit, Output, EventEmitter, forwardRef } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { FormControl } from '@angular/forms';

import creditCardType, { types as CardType } from 'credit-card-type';
import { IconDefinition, faQuestionCircle } from '@fortawesome/free-solid-svg-icons';

import { NewPaymentMethodService } from '../../services/new-payment-method.service';
import { TranslationService } from '@acmetocasino/shared';

import {
  Validators,
  FormGroup,
  ControlValueAccessor,
  Validator,
  AbstractControl,
  ValidationErrors,
  NG_VALUE_ACCESSOR,
  NG_VALIDATORS
} from '@angular/forms';
import { cardRegSteps, stepId } from './pxp-add-card.constants';

const ACCEPTED_CARD_TYPES = [
  'AMERICAN_EXPRESS',
  'DISCOVER',
  'MASTERCARD',
  'VISA'
];

@Component({
  selector: 'acme-cashier-pxp-add-card',
  templateUrl: './pxp-add-card.component.html',
  styleUrls: ['./pxp-add-card.component.scss'],
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => PxpAddCardComponent),
      multi: true
    },
    {
      provide: NG_VALIDATORS,
      useExisting: forwardRef(() => PxpAddCardComponent),
      multi: true
    }
  ]
})
export class PxpAddCardComponent implements OnInit, ControlValueAccessor, Validator {
  public faQuestionCircle: IconDefinition = faQuestionCircle;
  public stepId: typeof stepId = stepId;
  public firstAndLastNameFieldsDisabled = false;

  @Input() amount: string = '';
  @Input() creditCardsAllowed: boolean;
  @Input() userJurisdiction?: string;

  @Input() set cardholderNameRequired(value: boolean) {
    if (!value) {
      this.cardForm.controls.firstName.clearValidators();
      this.cardForm.controls.firstName.updateValueAndValidity();
      this.cardForm.controls.lastName.clearValidators();
      this.cardForm.controls.lastName.updateValueAndValidity();
    }
    this.firstAndLastNameFieldsDisabled = !value;
  }

  @Output() onCardDetailsEntered: EventEmitter<string> = new EventEmitter<string>();

  // Input mask: starts with 2,4,5,6 (Visa/MC/Amex/Discover BIN ranges)
  // Spaces inserted every 4 digits, up to 19 digits + 4 spaces = 23 chars
  public cardNumberMask = [
    /(2|4|5|6)/, /\d/, /\d/, /\d/, ' ',
    /\d/, /\d/, /\d/, /\d/, ' ',
    /\d/, /\d/, /\d/, /\d/, ' ',
    /\d/, /\d/, /\d/, /\d/, ' ',
    /\d/, /\d/, /\d/
  ];

  public cardLengths = {
    max: { card: 23, cvv: 3, month: 2, year: 2 },
    min: { card: 19, cvv: 3, month: 2, year: 2 },
  };

  public numericReg = '^[0-9]+$';
  public namesReg = /^[A-Za-z \u2018\u2019'-]+$/;

  public cardForm: FormGroup = new FormGroup({
    cardNumber: new FormControl('', [
      Validators.required,
      Validators.minLength(19),
      Validators.maxLength(23)
    ]),
    firstName: new FormControl('', [
      Validators.required,
      Validators.pattern(this.namesReg)
    ]),
    lastName: new FormControl('', [
      Validators.required,
      Validators.minLength(2),
      Validators.pattern(this.namesReg)
    ]),
    cardType: new FormControl('', [Validators.required]),
    cardCvv: new FormControl('', [
      Validators.required,
      Validators.pattern(this.numericReg),
      Validators.minLength(3),
      Validators.maxLength(3)
    ]),
    expiryMonth: new FormControl('', [
      Validators.required,
      Validators.minLength(2),
      Validators.maxLength(2)
    ]),
    expiryYear: new FormControl('', [
      Validators.required,
      Validators.minLength(2),
      Validators.maxLength(2),
      Validators.pattern(this.numericReg)
    ])
  });

  constructor(
    public newPaymentMethodService: NewPaymentMethodService,
    public route: ActivatedRoute,
    private translate: TranslationService
  ) {}

  ngOnInit() {
    // Auto-detect card type from BIN range as user types
    this.cardForm.get('cardNumber').valueChanges.subscribe(selectedValue => {
      if (selectedValue) {
        const cardType = creditCardType(selectedValue.toString()).filter(card =>
          ACCEPTED_CARD_TYPES.map(type => card.type === CardType[type])
        );

        if (cardType.length > 0) {
          // Amex has 4-digit CVV, others have 3
          this.cardLengths.max.cvv = cardType[0].code.size;
          this.cardForm.get('cardType').setValue(`pxp_card_${cardType[0].type}`);
        }
      } else {
        this.cardForm.get('cardType').setValue(undefined);
      }
    });

    // Cross-validate month and year for card expiry
    this.cardForm.get('expiryMonth').valueChanges.subscribe(month => {
      this.validateExpiry(month, this.cardForm.get('expiryYear').value);
    });

    this.cardForm.get('expiryYear').valueChanges.subscribe(year => {
      this.validateExpiry(this.cardForm.get('expiryMonth').value, year);
    });

    this.cardForm.valueChanges.subscribe(() => {
      this.onCardDetailsEntered.emit(this.cardForm.value);
    });
  }

  private validateExpiry(month: string, year: string) {
    const currentMonth = new Date().getMonth() + 1;
    const currentYear = parseInt(
      new Date().getFullYear().toString().substr(2, 2), 10
    );
    const expiryMonth = parseInt(month, 10);
    const expiryYear = parseInt(year, 10);

    const isExpired = expiryYear < currentYear ||
      (expiryYear === currentYear && expiryMonth < currentMonth);

    this.cardForm.get('expiryMonth').setErrors(
      isExpired ? { 'card-expired': true } : null
    );
    this.cardForm.get('expiryYear').setErrors(
      isExpired ? { 'card-expired': true } : null
    );
  }

  // Auto-advance to next field when current field reaches max length
  onKeyUp($event) {
    const inputField = $event.srcElement;
    const maxLength = parseInt(inputField.attributes['maxlength'].value, 10);
    let stepIndex = cardRegSteps.indexOf(inputField.id);

    if (inputField.value.length >= maxLength) {
      stepIndex += 1;
      document.getElementById(cardRegSteps[stepIndex]).focus();
    }
  }

  // Month input validation: only allow valid month digits
  onMonthInputChange(event) {
    const isValidChar =
      (event.target.value === '' && /[0-1]/.test(event.key)) ||
      (event.target.value === '0' && /[1-9]/.test(event.key)) ||
      (event.target.value === '1' && /[0-2]/.test(event.key));

    if (!isValidChar) {
      event.preventDefault();
    }
  }

  // Prevent numeric characters in name fields
  disallowNumericCharacters(event: any): any {
    if (/[0-9\+\-]/.test(event.key)) {
      event.preventDefault();
      return false;
    }
  }

  // ControlValueAccessor implementation
  onChange = (value: number) => {};
  onTouched = () => {};

  registerOnChange(fn: (value: number) => void): void {
    this.cardForm.valueChanges.subscribe(fn);
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  writeValue(value: any) {
    value && this.cardForm.setValue(value, { emitEvent: false });
  }

  validate(c: AbstractControl): ValidationErrors | null {
    return this.cardForm.valid ? null : {
      invalidForm: { valid: false, message: 'Card form fields are invalid' }
    };
  }
}
