#![cfg(test)]

//! Regression coverage for a key held across the sign-in menus.

use super::*;
use pretty_assertions::assert_eq;

#[tokio::test]
async fn held_enter_requires_a_fresh_confirmation_to_advance_api_setup() {
    let (mut widget, _home) = tests::widget_forced_chatgpt().await;
    widget.auth_config.forced_login_method = None;
    let enter = |kind| KeyEvent::new_with_kind(KeyCode::Enter, KeyModifiers::NONE, kind);

    widget.handle_key_event(KeyCode::Up.into());
    widget.handle_key_event(enter(KeyEventKind::Press));
    widget.handle_key_event(enter(KeyEventKind::Repeat));
    widget.handle_key_event(enter(KeyEventKind::Press));
    widget.handle_key_event(KeyCode::Down.into());
    widget.handle_key_event(enter(KeyEventKind::Repeat));
    assert_eq!(widget.highlighted_mode, SignInOption::ApiKey);
    assert!(matches!(
        *widget.sign_in_state.read().unwrap(),
        SignInState::PickMode
    ));

    widget.handle_key_event(enter(KeyEventKind::Release));
    widget.handle_key_event(enter(KeyEventKind::Press));
    {
        let mut state = widget.sign_in_state.write().unwrap();
        let SignInState::ApiKeyEntry(input) = &mut *state else {
            panic!("expected API key entry");
        };
        input.value = "test-api-key".into();
        assert!(matches!(input.field, ApiSetupField::BaseUrl));
    }
    let (request_frame, mut frames) = FrameRequester::test_channel();
    widget.request_frame = request_frame;
    widget.handle_key_event(enter(KeyEventKind::Press));
    widget.handle_key_event(enter(KeyEventKind::Repeat));
    widget.handle_key_event(KeyCode::Left.into());
    widget.handle_key_event(enter(KeyEventKind::Repeat));
    {
        let state = widget.sign_in_state.read().unwrap();
        let SignInState::ApiKeyEntry(input) = &*state else {
            panic!("expected API setup to remain open");
        };
        assert!(matches!(input.field, ApiSetupField::BaseUrl));
    }

    widget.handle_key_event(enter(KeyEventKind::Release));
    widget.handle_key_event(enter(KeyEventKind::Press));
    frames.try_recv().expect("fresh Enter should advance setup");
    let state = widget.sign_in_state.read().unwrap();
    let SignInState::ApiKeyEntry(input) = &*state else {
        panic!("expected API setup to remain open");
    };
    assert!(matches!(input.field, ApiSetupField::Model));
    assert_eq!(input.value, "test-api-key");
}
