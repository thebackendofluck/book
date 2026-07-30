// Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
// https://thebackendofluck.com | https://github.com/thebackendofluck/book
// SPDX-License-Identifier: Apache-2.0
//
// FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
// Published to demonstrate the patterns explained in the book. This code is
// not certified for real-money gaming: operating a gambling platform requires
// your own licence, independent test-lab certification (GLI, eCOGRA or
// equivalent) and regulator approval.

// AcmetoCasino Prize Admin - Game Qualifier Form
// Links specific games to campaign eligibility criteria.
// A "game qualifier" defines which games count toward a promotion's
// wagering requirements or trigger prize distribution.
//
// Example: A "Slots Tournament" campaign might qualify only specific
// slot games. Players wagering on non-qualifying games wouldn't
// accumulate tournament points or earn prizes.
//
// This connects directly to the casino mathematics in Chapter 13:
// different games have different RTPs and volatilities, and qualifying
// only certain games lets operators control the effective cost of
// promotions by excluding high-RTP games that would make bonuses
// unprofitable.

import { Component } from '@angular/core';
import { FormControl, FormGroup, Validators } from '@angular/forms';
import { SimpleCreateEditViewFormComponent } from '../../../shared/components/simple-create-edit-view-form/simple-create-edit-view-form.component';
import { FormState } from '../../../shared/components/simple-create-edit-view-form/form-state.enum';
import {
  GameQualifier,
  GameQualifierAttribute,
  GameQualifierAttributeLabels,
  GameQualifierFormData
} from '../../../shared/models/game-qualifier.model';
import { GameQualifierService } from '../../../shared/services/game-qualifier.service';
import { DropdownItem } from '../../../interfaces/dropdown-item.interface';
import { GameService } from '../../../shared/services/game.service';
import { GameAttribute } from '../../../shared/models/game.model';
import { EndpointModel } from '../../../shared/models/endpoint-model.model';

@Component({
  selector: 'app-game-qualifier-form',
  templateUrl: './game-qualifier-form.component.html',
  styleUrls: ['./game-qualifier-form.component.scss']
})
export class GameQualifierFormComponent
  extends SimpleCreateEditViewFormComponent<GameQualifier, GameQualifierFormData> {

  public gameQualifierAttribute = GameQualifierAttribute;
  public gameQualifierAttributeLabel = GameQualifierAttributeLabels;
  public games: DropdownItem[] = [];

  constructor(
    gameQualifierService: GameQualifierService,
    private gameService: GameService
  ) {
    super(gameQualifierService);

    // Load all available games for the qualifier dropdown
    this.gameService.getAll().subscribe(
      (games: EndpointModel[]) => {
        this.games = games.map((game: EndpointModel): DropdownItem => ({
          id: game[GameAttribute.ID],
          name: game[GameAttribute.NAME]
        }));
      }
    );
  }

  protected initForm(): void {
    this.form = new FormGroup({
      [GameQualifierAttribute.ID]:
        new FormControl({ value: '', disabled: true }, Validators.required),
      [GameQualifierAttribute.NAME]:
        new FormControl(
          { value: '', disabled: this.formState === FormState.VIEW },
          Validators.required
        ),
      [GameQualifierAttribute.GAME_ID]:
        new FormControl(
          { value: '', disabled: this.formState === FormState.VIEW },
          Validators.required
        )
    });
    super.initForm();
  }

  protected getFormData(): GameQualifierFormData {
    return {
      [GameQualifierAttribute.NAME]:
        this.form.value[GameQualifierAttribute.NAME],
      [GameQualifierAttribute.GAME_ID]:
        this.form.value[GameQualifierAttribute.GAME_ID]
    };
  }

  public resetValues(): void {
    const gameQualifier = this.model as GameQualifier;
    this.form.patchValue({
      [GameQualifierAttribute.ID]:
        gameQualifier[GameQualifierAttribute.ID],
      [GameQualifierAttribute.NAME]:
        gameQualifier[GameQualifierAttribute.NAME],
      [GameQualifierAttribute.GAME_ID]:
        gameQualifier[GameQualifierAttribute.GAME_ID]
    });
  }
}
