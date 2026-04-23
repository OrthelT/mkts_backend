WCMKTnorth2
DOCTRINE ID     DOCTRINE NAME            FRIENDLY NAME
99              Special Fits             Special Fits
100             cynos                    Cynos
95              SUBS - WC Maelstroms     Maelstroms
98              SUBS - WC-EN Muninn      Muninn
97              SUBS - WC-EN Ferox       Ferox
SELECT doctrine_id, fit_id, ship_name FROM doctrine_fits WHERE doctrine_id in (99,100,95,98,97);

┌─────────────┬────────┬────────────┐
│ doctrine_id │ fit_id │ ship_name  │
├─────────────┼────────┼────────────┤
│ 99          │ 991    │ Kestrel    │LEAD
│ 100         │ 155    │ Falcon     │
│ 100         │ 156    │ Rapier     │
│ 100         │ 157    │ Pilgrim    │
│ 100         │ 158    │ Arazu      │ LEAD
│ 95          │ 134    │ Hyena      │
│ 95          │ 184    │ Flycatcher │
│ 95          │ 242    │ Crucifier  │
│ 95          │ 323    │ Maulus     │
│ 95          │ 351    │ Onyx       │
│ 95          │ 354    │ Scimitar   │
│ 95          │ 355    │ Claymore   │
│ 95          │ 356    │ Svipul     │ LEAD 550
│ 95          │ 361    │ Huginn     │
│ 95          │ 364    │ Lachesis   │
│ 95          │ 397    │ Basilisk   │
│ 95          │ 450    │ Sabre      │
│ 95          │ 451    │ Broadsword │
│ 95          │ 463    │ Loki       │
│ 95          │ 475    │ Vulture    │
│ 98          │ 554    │ Muninn     │ LEAD
│ 98          │ 134    │ Hyena      │
│ 98          │ 184    │ Flycatcher │
│ 98          │ 351    │ Onyx       │
│ 98          │ 354    │ Scimitar   │
│ 98          │ 355    │ Claymore   │
│ 98          │ 356    │ Svipul     │
│ 98          │ 361    │ Huginn     │
│ 98          │ 364    │ Lachesis   │
│ 98          │ 450    │ Sabre      │
│ 98          │ 451    │ Broadsword │
│ 98          │ 462    │ Nighthawk  │
│ 98          │ 463    │ Loki       │
│ 97          │ 552    │ Ferox      │ 
│ 97          │ 553    │ Ferox      │ LEAD
│ 97          │ 134    │ Hyena      │
│ 97          │ 39     │ Drake      │
│ 97          │ 119    │ Scythe     │
│ 97          │ 184    │ Flycatcher │
│ 97          │ 450    │ Sabre      │
│ 97          │ 129    │ Osprey     │
│ 97          │ 135    │ Keres      │
└─────────────┴────────┴────────────┘


WCMKTprod
┌─────────────┬─────────────────────┬───────────────┐
│ doctrine_id │    doctrine_name    │ friendly_name │
├─────────────┼─────────────────────┼───────────────┤
│ 44          │ SUBS - WCEN Entosis │ Entosis       │
│ 99          │ Special Fits        │ Special Fits  │
│ 100         │ cynos               │ Cynos         │
└─────────────┴─────────────────────┴───────────────┘
SELECT doctrine_id, fit_id, ship_name FROM doctrine_fits WHERE doctrine_id in (44,99,100);
┌─────────────┬────────┬───────────┐
│ doctrine_id │ fit_id │ ship_name │
├─────────────┼────────┼───────────┤
│ 44          │ 287    │ Vulture   │
│ 44          │ 288    │ Drake     │ LEAD
│ 44          │ 289    │ Nereus    │
│ 44          │ 291    │ Widow     │
│ 44          │ 292    │ Porpoise  │
│ 99          │ 991    │ Kestrel   │ LEAD
│ 100         │ 155    │ Falcon    │
│ 100         │ 156    │ Rapier    │
│ 100         │ 157    │ Pilgrim   │ 
│ 100         │ 158    │ Arazu     │ LEAD
└─────────────┴────────┴───────────┘
