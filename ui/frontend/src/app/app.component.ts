import { Component } from '@angular/core';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, MatToolbarModule, MatIconModule, MatButtonModule],
  template: `
    <mat-toolbar class="app-toolbar">
      <a class="brand" routerLink="/">
        <mat-icon>local_hospital</mat-icon>
        Medical Pseudonymizer
      </a>
      <span class="spacer"></span>
      <a class="nav-link" routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{exact:true}">
        Analyze
      </a>
      <a class="nav-link" routerLink="/history" routerLinkActive="active">
        History
      </a>
    </mat-toolbar>
    <router-outlet />
  `
})
export class AppComponent {}
